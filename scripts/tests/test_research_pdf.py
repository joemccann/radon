"""Real Firecrawl and PDFium evidence roundtrip on an authored synthetic PDF."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

PARSER_AVAILABLE=importlib.util.find_spec("pdf_inspector") is not None and importlib.util.find_spec("pypdfium2") is not None
pytestmark=pytest.mark.skipif(not PARSER_AVAILABLE,reason="Firecrawl/PDFium runtime dependencies unavailable")


def make_pdf(path, pages=2, size=(300,200)):
    # Standard PDF objects/xref, authored text plus a blue chart rectangle.
    objects=[b"<< /Type /Catalog /Pages 2 0 R >>",b"",b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    page_ids=[]
    for index in range(pages):
        page_id=len(objects)+1;stream_id=page_id+1;page_ids.append(page_id)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {size[0]} {size[1]}] /Resources << /Font << /F1 3 0 R >> >> /Contents {stream_id} 0 R >>".encode())
        stream=f"BT /F1 12 Tf 20 175 Td (Research evidence page {index+1}, September 7 2026) Tj ET 0 0 1 rg 60 40 180 80 re f".encode()
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode()+stream+b"\nendstream")
    objects[1]=f"<< /Type /Pages /Count {pages} /Kids [{' '.join(str(i)+' 0 R' for i in page_ids)}] >>".encode()
    output=bytearray(b"%PDF-1.4\n");offsets=[0]
    for index,obj in enumerate(objects,1):
        offsets.append(len(output));output.extend(f"{index} 0 obj\n".encode()+obj+b"\nendobj\n")
    start=len(output);output.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:output.extend(f"{offset:010} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    path.write_bytes(output)
    return path


def test_real_parse_original_pages_and_crop(tmp_path):
    from research.pdf import parse,render
    from PIL import Image
    pdf=make_pdf(tmp_path/"source.pdf")
    evidence=parse(pdf,tmp_path/"parsed")
    assert evidence["parser"]=="firecrawl/pdf-inspector" and evidence["page_count"]==2
    assert evidence["source_sha256"]==hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert [page["page_number"] for page in evidence["pages"]]==[1,2]
    assert "Research evidence page 2" in (tmp_path/"parsed/page-0002.md").read_text()
    assert any("Research" in item["text"] for item in evidence["pages"][0]["items"])
    original=render(pdf,tmp_path/"full",[2],dpi=144)[0]
    cropped=render(pdf,tmp_path/"crop",[2],dpi=144,crop=[.2,.4,.8,.8])[0]
    assert original["width"]==600 and original["height"]==400
    assert cropped["width"]==360 and cropped["height"]==160
    assert cropped["page_number"]==2 and cropped["source_sha256"]==evidence["source_sha256"]
    with Image.open(tmp_path/"full"/original["image_file"]) as full,Image.open(tmp_path/"crop"/cropped["image_file"]) as crop:
        assert full.crop((120,160,480,320)).tobytes()==crop.tobytes()


def test_real_isolated_subprocess_extract_and_render(tmp_path,monkeypatch):
    from research.pipeline import Pipeline
    scripts=str(Path(__file__).resolve().parents[1])
    monkeypatch.setenv("PYTHONPATH",scripts+os.pathsep+os.environ.get("PYTHONPATH",""))
    pdf=make_pdf(tmp_path/"source.pdf")
    pipe=Pipeline(tmp_path,None,None)
    evidence=pipe.extract(pdf,tmp_path/"parsed")
    crop=pipe.render_isolated(pdf,tmp_path/"rendered",[2],144,[.2,.4,.8,.8])
    assert evidence["page_count"]==2 and crop[0]["page_number"]==2
    assert crop[0]["crop_normalized_top_left"]==[.2,.4,.8,.8]
    assert (tmp_path/"rendered"/crop[0]["image_file"]).stat().st_mode & 0o777==0o600


@pytest.mark.parametrize("kwargs", [{"dpi":71},{"dpi":301},{"crop":[0,0,2,1]},{"crop":[0,0,1]},{"pages":[0]},{"pages":[3]}])
def test_invalid_render_configuration_rejected(tmp_path,kwargs):
    from research.pdf import render
    pdf=make_pdf(tmp_path/"source.pdf")
    args={"pages":[1]}|kwargs
    with pytest.raises(ValueError):render(pdf,tmp_path/"render",**args)


def test_page_and_pixel_budgets_reject_hostile_size(tmp_path):
    from research.pdf import parse,render
    excessive=make_pdf(tmp_path/"many.pdf",pages=101)
    with pytest.raises(ValueError,match="1..100"):parse(excessive,tmp_path/"parsed")
    huge=make_pdf(tmp_path/"huge.pdf",pages=1,size=(20000,20000))
    with pytest.raises(ValueError,match="pixel budget"):render(huge,tmp_path/"rendered",[1])


def test_render_only_cli_requires_pages(tmp_path):
    pdf=make_pdf(tmp_path/"source.pdf")
    env=os.environ|{"PYTHONPATH":str(Path(__file__).resolve().parents[1])}
    result=subprocess.run([sys.executable,"-m","research.pdf",str(pdf),str(tmp_path/"out"),"--render-only"],capture_output=True,timeout=20,env=env)
    assert result.returncode==2 and b"requires --pages" in result.stderr


def test_pdf_cli_parse_then_render_json(tmp_path,monkeypatch,capsys):
    from research import pdf as module
    source=make_pdf(tmp_path/"source.pdf")
    monkeypatch.setattr(sys,"argv",["research.pdf",str(source),str(tmp_path/"output"),"--pages","1","--crop","0,0,1,1"])
    module.main()
    records=[json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[0]["page_count"]==2 and records[1][0]["page_number"]==1


def test_pdf_cli_render_only_missing_pages_direct(tmp_path,monkeypatch):
    from research import pdf as module
    monkeypatch.setattr(sys,"argv",["research.pdf","unused.pdf",str(tmp_path),"--render-only"])
    with pytest.raises(SystemExit) as error:module.main()
    assert error.value.code==2


@pytest.mark.parametrize("hard",[-1,1_073_741_824])
def test_native_entrypoint_installs_linux_resource_limits(tmp_path,monkeypatch,hard):
    import resource
    import runpy
    from research import pdf as module
    source=make_pdf(tmp_path/"source.pdf")
    calls=[]
    monkeypatch.setattr(sys,"argv",["research.pdf",str(source),str(tmp_path/"out"),"--render-only","--pages","1"])
    monkeypatch.setattr(sys,"platform","linux")
    monkeypatch.setattr(resource,"RLIM_INFINITY",-1)
    monkeypatch.setattr(resource,"getrlimit",lambda kind:(hard,hard))
    monkeypatch.setattr(resource,"setrlimit",lambda *args:calls.append(args))
    monkeypatch.setattr(os,"umask",lambda _:0o077)
    runpy.run_path(module.__file__,run_name="__main__")
    assert (resource.RLIMIT_CPU,(150,150)) in calls
    expected=2_147_483_648 if hard==-1 else hard
    assert (resource.RLIMIT_AS,(expected,expected)) in calls


def test_pdfium_anchor_frame_matches_rendered_text(tmp_path):
    from research.pdf import text_anchors,render
    from PIL import Image
    source=make_pdf(tmp_path/"source.pdf")
    record=text_anchors(source,[1])[0]
    assert record["frame"].startswith("PDFium unrotated zero-origin")
    anchor=next(a for a in record["anchors"] if "Research evidence" in a["text"])
    assert all(0<=coordinate<=1 for coordinate in anchor["box"])
    rendered=render(source,tmp_path/"rendered",[1],144)[0]
    with Image.open(tmp_path/"rendered"/rendered["image_file"]) as image:
        left,top,right,bottom=anchor["box"]
        crop=image.crop((int(left*image.width),int(top*image.height),int(right*image.width)+1,int(bottom*image.height)+1))
        assert crop.convert("L").getextrema()[0]<100


@pytest.mark.parametrize("change",["rotation","cropbox"])
def test_anchor_localization_refuses_unknown_coordinate_frames(tmp_path,change):
    import pypdfium2
    from research.pdf import text_anchors
    original=make_pdf(tmp_path/"original.pdf")
    modified=tmp_path/"modified.pdf"
    with pypdfium2.PdfDocument(str(original)) as document:
        page=document[0]
        if change=="rotation":page.set_rotation(90)
        else:page.set_cropbox(10,10,290,190)
        document.save(str(modified))
        page.close()
    assert text_anchors(modified,[1])[0]=={"page_number":1,"frame":"unavailable","anchors":[]}


def test_isolated_anchor_cli_and_invalid_pages(tmp_path,monkeypatch):
    from research.pdf import text_anchors
    from research.pipeline import Pipeline
    source=make_pdf(tmp_path/"source.pdf")
    monkeypatch.setenv("PYTHONPATH",str(Path(__file__).resolve().parents[1]))
    anchors=Pipeline(tmp_path,None,None).anchors_isolated(source,[1])
    assert anchors[0]["anchors"]
    with pytest.raises(ValueError):text_anchors(source,[0])

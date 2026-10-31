"""Cross-build a minimal Darwin-only bundle from locked native optional packages."""
from pathlib import Path
import json
import shutil
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[2]
BUILDER=ROOT/'docker/app/vendor-clis/install-darwin.cjs'
MACHO=b'\xcf\xfa\xed\xfe\x0c\x00\x00\x01'+b'fixture'


def source(tmp_path):
    root=tmp_path/'source';root.mkdir()
    package={'dependencies':{'@anthropic-ai/claude-code':'2.1.311','@openai/codex':'0.160.1','@xai-official/grok':'1.0.70'}}
    (root/'package.json').write_text(json.dumps(package))
    entries={}
    for name,version,binary in [('@anthropic-ai/claude-code-darwin-arm64','2.1.311','claude'),('@openai/codex-darwin-arm64','0.160.1-darwin-arm64','vendor/aarch64-apple-darwin/bin/codex'),('@xai-official/grok-darwin-arm64','1.0.70','bin/grok.br')]:
        folder=root/'node_modules'/name;folder.mkdir(parents=True)
        (folder/'package.json').write_text(json.dumps({'version':version}))
        target=folder/binary;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(MACHO)
        if name.startswith('@xai'):
            subprocess.run(['node','-e','const f=require("fs"),z=require("zlib");f.writeFileSync(process.argv[1],z.brotliCompressSync(f.readFileSync(process.argv[1])))',str(target)],check=True)
        entries['node_modules/'+name]={'version':version}
    rg=root/'node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/codex-path/rg';rg.parent.mkdir(parents=True);rg.write_bytes(MACHO)
    (root/'package-lock.json').write_text(json.dumps({'packages':entries}))
    helpers=tmp_path/'helpers';helpers.mkdir()
    for name in ('__init__.py','client.py','cli.py','cli_bridge.py'):(helpers/name).write_text('# public helper\n')
    return root,helpers


def build(root,helpers,out):
    assert BUILDER.is_file(), 'Darwin builder is required'
    return subprocess.run(['node','-e','require(process.argv[1]).materialize({sourceRoot:process.argv[2],helpersRoot:process.argv[3],targetRoot:process.argv[4]})',str(BUILDER),str(root),str(helpers),str(out)],capture_output=True,text=True)


@pytest.mark.skipif(not shutil.which('node'),reason='Node toolchain unavailable')
def test_cross_builder_exports_only_native_executables_and_public_helpers(tmp_path):
    root,helpers=source(tmp_path);out=tmp_path/'out'
    result=build(root,helpers,out)
    assert result.returncode==0,result.stderr
    files={str(path.relative_to(out)) for path in out.rglob('*') if path.is_file()}
    assert files=={'package.json',*(f'bin/{name}' for name in ('claude','codex','grok','rg')),*(f'model_gateway/{name}' for name in ('__init__.py','client.py','cli.py','cli_bridge.py'))}
    assert all((out/'bin'/name).read_bytes()==MACHO for name in ('claude','codex','grok','rg'))
    assert not any(path.is_symlink() for path in out.rglob('*'))


def test_cross_builder_refuses_wrong_architecture_before_export(tmp_path):
    root,helpers=source(tmp_path)
    (root/'node_modules/@anthropic-ai/claude-code-darwin-arm64/claude').write_bytes(b'\x7fELF-Linux')
    result=build(root,helpers,tmp_path/'out')
    assert result.returncode!=0


def test_cross_builder_refuses_package_pin_drift(tmp_path):
    root,helpers=source(tmp_path)
    (root/'node_modules/@anthropic-ai/claude-code-darwin-arm64/package.json').write_text('{"version":"latest"}')
    result=build(root,helpers,tmp_path/'out')
    assert result.returncode!=0


def test_darwin_payload_and_native_smoke_are_required_image_gates():
    docker=(ROOT/'docker/app/Dockerfile.python').read_text()
    workflow=(ROOT/'.github/workflows/app-images.yml').read_text()
    assert 'AS vendor-clis-darwin' in docker
    assert '--from=vendor-clis-darwin /opt/radon-darwin /opt/radon-darwin' in docker
    assert 'darwin-clients:' in workflow and 'runs-on: macos-15' in workflow
    assert 'codesign --verify --strict' in workflow
    assert 'check_model_cli_transport.py' in workflow
    assert 'npm ci --ignore-scripts' in workflow

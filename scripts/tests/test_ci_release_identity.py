"""Image/tag version selection shares admission and cannot mask CLI failure."""
from pathlib import Path
import os
import subprocess

import pytest
import yaml

ROOT=Path(__file__).resolve().parents[2]


def selection_steps():
    images=yaml.safe_load((ROOT/'.github/workflows/app-images.yml').read_text())
    ci=yaml.safe_load((ROOT/'.github/workflows/ci.yml').read_text())
    image=next(step['run'] for step in images['jobs']['node-image']['steps'] if step.get('id')=='release')
    tag=next(step['run'] for step in ci['jobs']['release-tag']['steps'] if step.get('name')=='Tag the deployed version')
    return image,tag


@pytest.mark.parametrize('index', [0,1], ids=['image','tag'])
def test_selector_failure_does_not_publish_output_or_tag(tmp_path,index):
    executable=tmp_path/'python3'
    executable.write_text('#!/bin/sh\nprintf "0.7.0\\n"\nexit 37\n');executable.chmod(0o755)
    git=tmp_path/'git';marker=tmp_path/'git-called'
    git.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$MARKER"\nexit 0\n');git.chmod(0o755)
    output=tmp_path/'output'
    result=subprocess.run(['/bin/bash','-e','-o','pipefail','-c',selection_steps()[index]],cwd=tmp_path,
        env={'PATH':str(tmp_path)+os.pathsep+os.defpath,'GITHUB_OUTPUT':str(output),'GITHUB_SHA':'a'*40,'MARKER':str(marker)},
        capture_output=True,text=True,timeout=10)
    assert result.returncode==37,result.stdout+result.stderr
    assert not output.exists() or output.read_text()==''
    assert not marker.exists()


def test_image_and_tag_select_the_same_exact_sha_validated_identity():
    for command in selection_steps():
        assert 'scripts/release_version.py --ci-version --expected-sha "$GITHUB_SHA"' in command
    ci=yaml.safe_load((ROOT/'.github/workflows/ci.yml').read_text())
    tag=ci['jobs']['release-tag']
    assert tag['needs']==['deploy']
    assert "needs.deploy.result == 'success'" in tag['if']
    assert 'refs/tags/${tag}' in selection_steps()[1]

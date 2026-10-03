"""REL-108 / R-315: execute image loading and GET admission with a synthetic disk cache."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = r'''
const fs = require('node:fs'), vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync(input.root + '/web/app/api/menthorq/cta/image/route.tsx', 'utf8');
let fontCalls = 0, renders = 0;
// Execute the production loader and GET admission prefix. The image renderer
// is the fake boundary; Python CI deliberately has no frontend dependencies.
const start = source.indexOf('async function loadLatestCta(');
const end = source.indexOf('\nfunction SectionHeader', start);
let loader = source.slice(start, end)
  .replace('section?: string', 'section')
  .replace('): Promise<CtaCache | null>', ')')
  .replace(' as CtaCache', '')
  .replace(': Record<string, CtaRow[]>', '');
const getStart = source.indexOf('export async function GET(');
const getEnd = source.indexOf('  const fonts = await loadFonts();', getStart);
const get = source.slice(getStart, getEnd).replace('export async', 'async')
  .replace('request: Request', 'request') +
  '  await loadFonts(); return render(); }';
if (start < 0 || end < 0 || getStart < 0 || getEnd < 0) throw Error('admission boundary missing');
const context = vm.createContext({Response, URL, CACHE_DIR: '/mock/cache',
  readdir: async () => ['cta_mock.json'], readFile: async () => JSON.stringify(input.cache),
  join: require('node:path').join, reconcileCtaTables: value => value,
  deduplicateTables: value => value,
  requireRouteAccess: async () => ({ok: true}),
  loadFonts: async () => {fontCalls++; return [];},
  render: () => {renders++; return new Response('mock image');}});
vm.runInContext(loader + '\n' + get + '\nglobalThis.GET = GET;', context);
context.GET({url: 'http://mock/api/menthorq/cta/image' + input.query}).then(async response => {
  console.log(JSON.stringify({status: response.status, fontCalls, renders, body: await response.text()}));
}).catch(error => {console.error(error); process.exitCode = 1;});
'''


def execute(cache, query):
    node = shutil.which('node')
    assert node, 'Node required for isolated image-route fault injection'
    result = subprocess.run([node, '-e', HARNESS],
                            input=json.dumps({'root': str(ROOT), 'cache': cache, 'query': query}),
                            capture_output=True, text=True, timeout=15, check=True,
                            env={'PATH': str(Path(node).parent)})
    return json.loads(result.stdout)


@pytest.mark.parametrize('section', ['missing', '__proto__', 'constructor'])
def test_unknown_section_refuses_before_fonts_or_render(section):
    result = execute({'date': 'mock', 'tables': {'main': []}}, '?section=' + section)
    assert result == {'status': 404, 'fontCalls': 0, 'renders': 0, 'body': 'No CTA data available'}


@pytest.mark.parametrize('query', ['', '?section=main'])
def test_known_section_retains_success(query):
    assert execute({'date': 'mock', 'tables': {'main': []}}, query)['status'] == 200

import {describe,expect,it} from 'vitest';
import {parseTicketChecklist} from '../lib/researchWorkbench/handoff';
const now=Date.parse('2026-09-10T12:00:00Z');
const base={version:1,ticker:'NVDA',createdAt:'2026-09-10T11:00:00Z',items:['Review capex evidence'],sources:[{title:'Disclosure',url:'https://example.com',passage:'Capex'}]};
describe('research ticket provenance',()=>{
 it('accepts bounded, recent same-ticker context',()=>expect(parseTicketChecklist(base,'NVDA',now)).toEqual(base));
 it.each([{createdAt:'2026-09-08T00:00:00Z'},{createdAt:'2026-09-11T00:00:00Z'},{ticker:'MSFT'},{items:[]},{sources:[{title:'x',url:'javascript:alert(1)'}]},{sources:[{title:'x',url:'https://secret@example.com'}]}])('rejects stale, mismatched or unsafe handoff %j',patch=>expect(parseTicketChecklist({...base,...patch},'NVDA',now)).toBeNull());
});

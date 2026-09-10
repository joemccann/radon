import {describe,expect,it} from 'vitest';
import {createInvestorWorkbook,createValuationWorkbook} from '../lib/researchWorkbench/artifacts';
describe('research workbook exports',()=>{
 it('keeps source text as literal strings, not executable formulas',async()=>{
  const book=await createInvestorWorkbook({title:'Update',asOf:'2026-09-10',snapshot:['=HYPERLINK("https://bad.example")'],whatsMoving:[],pipeline:[],headwinds:[],sources:[{title:'Source',url:'https://example.com',passage:'Revenue $10 million'}]});
  expect(book.worksheets.map(s=>s.name)).toEqual(['Snapshot','What’s moving','Pipeline','Headwinds','Sources']);
  expect(book.getWorksheet('Snapshot')!.getCell('B3').value).toBe('=HYPERLINK("https://bad.example")');
  expect(book.getWorksheet('Sources')!.getCell('C2').value).toBe('Revenue $10 million');
  const buffer=await book.xlsx.writeBuffer();expect(buffer.byteLength).toBeGreaterThan(1000);
 });
 it('exports editable assumptions and formulas with matching cached results',async()=>{
  const book=await createValuationWorkbook({ticker:'Example',cashFlow:100,growth:0,discountRate:.1,terminalGrowth:0,years:5,netDebt:200,shares:100,ebitda:200,entryMultiple:10,exitMultiple:10,debtFraction:.5,debtPaydown:100});
  const sheet=book.getWorksheet('Scenario')!;
  expect(sheet.getCell('B2').value).toBe(100);
  expect(sheet.getCell('B17').value).toEqual({formula:'$B$2*(1+$B$3)^A17',result:100});
  expect(sheet.getCell('B23').value).toMatchObject({formula:'SUM(C17:C21)+B22/(1+$B$4)^$B$6',result:expect.closeTo(1000)});
  expect(sheet.getCell('B31').value).toMatchObject({result:expect.closeTo(1.5**.2-1)});
  expect(book.calcProperties.fullCalcOnLoad).toBe(true);
 });
});

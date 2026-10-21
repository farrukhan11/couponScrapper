import fs from 'node:fs/promises';
import { SpreadsheetFile, Workbook } from '@oai/artifact-tool';

const base = 'C:/Users/farrukh.saleeem/Documents/GitHub/couponScrapper';
const searchCsv = await fs.readFile(`${base}/revomadic_coupon_google_results.csv`, 'utf8');
const codesCsv = await fs.readFile(`${base}/revomadic_coupon_codes_revealed.csv`, 'utf8');

const parse = (t) => t.trim().split(/\r?\n/).map(line => line.split(','));
const wb = Workbook.create();
const urlSheet = wb.worksheets.add('Google US URLs');
const codeSheet = wb.worksheets.add('Codes by Site');
urlSheet.getRangeByIndexes(0,0,parse(searchCsv).length,parse(searchCsv)[0].length).values = parse(searchCsv);
codeSheet.getRangeByIndexes(0,0,parse(codesCsv).length,parse(codesCsv)[0].length).values = parse(codesCsv);

for (const name of ['Google US URLs', 'Codes by Site']) {
  const ws = wb.worksheets.getItem(name);
  const used = ws.getUsedRange();
  used.format.font = { name: 'Arial', size: 10 };
  used.format.wrapText = true;
  used.format.borders = { preset: 'all', style: 'thin', color: '#D9D9D9' };
  ws.getRange('A1:H1').format = { fill: '#1F4E78', font: { name: 'Arial', size: 10, bold: true, color: '#FFFFFF' } };
  ws.freezePanes.freezeRows(1);
  used.format.autofitColumns();
}

wb.recalculate();
const out = `${base}/revomadic_coupon_research_codes_scraped.xlsx`;
const xlsx = await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(out);
console.log(out);

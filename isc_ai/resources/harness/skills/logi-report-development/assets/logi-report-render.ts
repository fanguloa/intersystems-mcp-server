// Playwright helper: render a TrakCare Logi report (websys.Report) as PDF from a logged-in
// session. Copy it into a Playwright harness that provides an authenticated TrakCare page.
// Usage: const r = await renderLogiReport(page, { reportId: '<id>', reportCode: '<code>',
//                                               params: { P1: '<value>' }, name: '<prefix>' });
import fs from 'node:fs';
import path from 'node:path';
import type { Page, Response } from '@playwright/test';

/**
 * Render a TrakCare Logi report (websys.Report) as PDF through the logged-in session and save it.
 * Read-only: it opens the same preview page TrakCare opens (jreportpreview.csp) and stores the
 * document bytes so a human or an agent can review them against the mockup.
 */
export interface LogiReportRequest {
  /** websys.Report row id (TREPORT). */
  reportId: string;
  /** websys.Report code (Code property). */
  reportCode: string;
  /** Report parameters by position name: { P1: '138', P2: '...' }. Only these values are sent:
   *  websys.Report Pn expressions are NOT evaluated by this direct URL. */
  params: Record<string, string>;
  /** File name prefix for the evidence files. */
  name: string;
  /** Output folder (default evidence/reports). */
  outDir?: string;
}

export interface LogiReportResult {
  pdf: string;
  bytes: number;
  pages: number;
  /** "status content-type url" of every response seen by the preview window. */
  responses: string;
}

export function previewUrl(request: LogiReportRequest): string {
  const query = new URLSearchParams({
    ID: request.reportId,
    reportexpression: '',
    report: request.reportCode,
    previewlocal: '0',
    a: 'a',
    TREPORT: request.reportId,
    TPRINTSERVER: '1',
    ...request.params,
  });
  return `jreportpreview.csp?${query.toString()}`;
}

/** Parses "P1=138;P2=abc" (also accepts "&" or "," as separators). */
export function parseParams(text: string | undefined): Record<string, string> {
  const params: Record<string, string> = {};
  for (const pair of (text ?? '').split(/[;&,]/)) {
    const [key, ...rest] = pair.split('=');
    if (key?.trim()) params[key.trim()] = rest.join('=').trim();
  }
  return params;
}

/** Counts PDF page objects (enough for a sanity check; no PDF library is needed). */
export function countPdfPages(pdf: Buffer): number {
  return (pdf.toString('latin1').match(/\/Type\s*\/Page(?!s)/g) ?? []).length;
}

async function capturePdf(popup: Page, file: string, seen: string[]): Promise<Buffer> {
  const isPdf = (response: Response) =>
    (response.headers()['content-type'] ?? '').toLowerCase().includes('application/pdf');
  popup.on('response', (response) => {
    seen.push(`${response.status()} ${response.headers()['content-type'] ?? ''} ${response.url().split('?')[0]}`);
  });
  const pdf = await popup.waitForResponse(isPdf, { timeout: 120_000 });
  // The browser hands the navigation to its PDF viewer, so response.body() is the viewer page.
  // Replay the same request with the session cookies to get the document bytes.
  const request = pdf.request();
  const replay = await popup.context().request.fetch(request.url(), {
    method: request.method(),
    headers: { ...(await request.allHeaders()) },
    data: request.postDataBuffer() ?? undefined,
    timeout: 120_000,
  });
  const body = await replay.body();
  fs.writeFileSync(file, body);
  return body;
}

/**
 * Opens the report preview from the logged-in TrakCare shell page (window.open keeps the
 * session and never reloads the shell) and saves the PDF plus diagnostics next to it.
 */
export async function renderLogiReport(page: Page, request: LogiReportRequest): Promise<LogiReportResult> {
  const outDir = request.outDir ?? path.join('evidence', 'reports');
  fs.mkdirSync(outDir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const base = path.join(outDir, `${request.name}-${stamp}`);

  const popupPromise = page.context().waitForEvent('page');
  await page.evaluate((url) => { window.open(url, 'TRAK_report'); }, previewUrl(request));
  const popup = await popupPromise;

  const seen: string[] = [];
  let body: Buffer | undefined;
  try {
    body = await capturePdf(popup, `${base}.pdf`, seen);
  } finally {
    fs.writeFileSync(`${base}.responses.txt`, seen.join('\n'));
    if (!popup.isClosed()) {
      if (!body) {
        // Failure diagnostics: what the preview window showed instead of the PDF
        await popup.screenshot({ path: `${base}.png`, fullPage: true }).catch(() => {});
        const text = await popup.locator('body').innerText().catch(() => '');
        fs.writeFileSync(`${base}.txt`, text);
      }
      await popup.close().catch(() => {});
    }
  }
  return { pdf: path.resolve(`${base}.pdf`), bytes: body.length, pages: countPdfPages(body), responses: seen.join('\n') };
}

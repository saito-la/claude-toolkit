// Playwright launcher + adapter dispatch + "open editable URL for review".
import { chromium } from 'playwright';
import { execFile } from 'node:child_process';
import * as tonton from './adapters/tonton.mjs';
import * as chousei from './adapters/chousei.mjs';
import * as yotei from './adapters/yotei.mjs';

const ADAPTERS = { tonton, chousei, yotei };
export const getAdapter = (tool) => ADAPTERS[tool];

export async function withBrowser(headless, fn) {
  const browser = await chromium.launch({ headless });
  try {
    const ctx = await browser.newContext({ locale: 'ja-JP', timezoneId: 'Asia/Tokyo' });
    const page = await ctx.newPage();
    return await fn(page);
  } finally {
    await browser.close();
  }
}

// Open a URL in the user's default GUI browser (macOS `open`) for visual review.
export function openForReview(url) {
  return new Promise((resolve) => {
    execFile('/usr/bin/open', [url], (err) => resolve(!err));
  });
}

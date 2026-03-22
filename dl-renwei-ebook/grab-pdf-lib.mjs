import { mkdir, writeFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { createDecipheriv } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline/promises';
import { createInterface as createLineInterface } from 'node:readline';
import process from 'node:process';

import { chromium } from 'playwright-core';
import * as pdfjsLib from 'pdfjs-dist/legacy/build/pdf.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = __dirname;
const defaultBrowserPath =
  process.env.PLAYWRIGHT_CHROME_PATH ||
  'C:\\Users\\ORR\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe';

class UserCancelledError extends Error {
  constructor(message = 'Cancelled by user.') {
    super(message);
    this.name = 'UserCancelledError';
  }
}

function parseArgs(argv) {
  const options = {
    url: '',
    outputDir: path.join(projectRoot, 'output'),
    profileDir: path.join(projectRoot, '.playwright-profile'),
    browserPath: defaultBrowserPath,
    waitAfterLoginMs: 2500,
    maxAttempts: 3,
    match: '',
    timeoutMs: 45000,
    pdfPassword: '',
    singleSegment: false,
    guiBridge: false,
    showHelp: false
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];

    if (!arg.startsWith('--') && !options.url) {
      options.url = arg;
      continue;
    }

    switch (arg) {
      case '--url':
        options.url = argv[index + 1] ?? '';
        index += 1;
        break;
      case '--output-dir':
        options.outputDir = path.resolve(argv[index + 1] ?? options.outputDir);
        index += 1;
        break;
      case '--profile-dir':
        options.profileDir = path.resolve(argv[index + 1] ?? options.profileDir);
        index += 1;
        break;
      case '--browser-path':
        options.browserPath = path.resolve(argv[index + 1] ?? options.browserPath);
        index += 1;
        break;
      case '--wait-after-login-ms':
        options.waitAfterLoginMs = Number(argv[index + 1] ?? options.waitAfterLoginMs);
        index += 1;
        break;
      case '--max-attempts':
        options.maxAttempts = Number(argv[index + 1] ?? options.maxAttempts);
        index += 1;
        break;
      case '--match':
        options.match = argv[index + 1] ?? '';
        index += 1;
        break;
      case '--timeout-ms':
        options.timeoutMs = Number(argv[index + 1] ?? options.timeoutMs);
        index += 1;
        break;
      case '--pdf-password':
        options.pdfPassword = argv[index + 1] ?? '';
        index += 1;
        break;
      case '--single-segment':
        options.singleSegment = true;
        break;
      case '--gui-bridge':
        options.guiBridge = true;
        break;
      case '--help':
      case '-h':
        options.showHelp = true;
        break;
      default:
        throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return options;
}

function printHelp() {
  console.log(`
Usage:
  npm run grab:pdf -- [url] [options]

Examples:
  npm run grab:pdf -- https://example.com/book/123
  npm run grab:pdf -- --url https://example.com/book/123 --match chapter01
  npm run grab:pdf -- --output-dir ./downloads

Options:
  --url <value>                  Optional start URL. If omitted, the script opens a blank page.
  --output-dir <path>            Folder for downloaded PDFs, merged PDFs, and metadata.
  --profile-dir <path>           Browser profile directory used to keep your login session.
  --browser-path <path>          Path to the Chrome/Chromium executable.
  --wait-after-login-ms <ms>     Extra wait time after you press Enter. Default: 2500.
  --max-attempts <n>             How many times to retry PDF detection. Default: 3.
  --match <text>                 Prefer PDF candidates whose URL includes this text.
  --timeout-ms <ms>              Navigation timeout. Default: 45000.
  --pdf-password <value>         Manually provide the PDF password if the page does not expose it.
  --single-segment               Only download the selected PDF instead of auto-combining detected segments.
  --gui-bridge                   Internal JSONL bridge used by the desktop GUI.
  --help, -h                     Show this help.
`.trim());
}

function ensurePositiveInteger(value, fallback) {
  return Number.isInteger(value) && value > 0 ? value : fallback;
}

function normalizeOptions(rawOptions) {
  const options = {
    ...rawOptions,
    outputDir: path.resolve(rawOptions.outputDir ?? path.join(projectRoot, 'output')),
    profileDir: path.resolve(rawOptions.profileDir ?? path.join(projectRoot, '.playwright-profile')),
    browserPath: path.resolve(rawOptions.browserPath ?? defaultBrowserPath)
  };

  options.maxAttempts = ensurePositiveInteger(options.maxAttempts, 3);
  options.waitAfterLoginMs = ensurePositiveInteger(Math.round(options.waitAfterLoginMs), 2500);
  options.timeoutMs = ensurePositiveInteger(Math.round(options.timeoutMs), 45000);
  options.pdfPassword = String(options.pdfPassword ?? '').trim();
  options.match = String(options.match ?? '');
  options.url = String(options.url ?? '');

  return options;
}


function sanitizeFilename(value, fallback = 'captured-pdf') {
  const cleaned = value
    .replace(/[<>:"/\\|?*\u0000-\u001F]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .toLowerCase();

  return cleaned || fallback;
}

function truncate(value, maxLength = 120) {
  return value.length <= maxLength ? value : `${value.slice(0, maxLength - 3)}...`;
}

function safeUrl(raw, baseUrl) {
  if (!raw) {
    return null;
  }

  if (raw.startsWith('blob:') || raw.startsWith('data:')) {
    return raw;
  }

  try {
    return new URL(raw, baseUrl).toString();
  } catch {
    return null;
  }
}

function extractViewerFile(url) {
  try {
    const parsed = new URL(url);
    const file = parsed.searchParams.get('file') ?? parsed.searchParams.get('src') ?? parsed.searchParams.get('url');
    return file ? safeUrl(file, url) : null;
  } catch {
    return null;
  }
}

function looksLikePdfUrl(url) {
  return (
    url.startsWith('blob:') ||
    url.startsWith('data:application/pdf') ||
    /\.pdf(\?|$)/i.test(url) ||
    /\/pdf(?:\/|$|\?)/i.test(url)
  );
}

function isLikelyPasswordValue(value) {
  if (typeof value !== 'string') {
    return false;
  }

  const trimmed = value.trim();

  if (!trimmed || trimmed.length > 128) {
    return false;
  }

  if (/^https?:/i.test(trimmed) || /^blob:/i.test(trimmed) || /^data:/i.test(trimmed)) {
    return false;
  }

  if ((trimmed.startsWith('{') || trimmed.startsWith('[') || trimmed.startsWith('<')) && trimmed.length > 20) {
    return false;
  }

  return true;
}

function looksLikePasswordError(error) {
  return /password/i.test(String(error?.message ?? error ?? ''));
}

function scoreCandidate(candidate, matchText) {
  let score = 0;

  if ((candidate.contentType ?? '').includes('pdf')) {
    score += 100;
  }
  if (/\.pdf(\?|$)/i.test(candidate.url)) {
    score += 40;
  }
  if (candidate.source === 'network-response') {
    score += 20;
  }
  if (candidate.source === 'viewer-file-param') {
    score += 15;
  }
  if (candidate.url.startsWith('blob:')) {
    score += 10;
  }
  if (matchText && candidate.url.toLowerCase().includes(matchText.toLowerCase())) {
    score += 50;
  }

  return score;
}

function normalizeBuffer(payload) {
  if (Buffer.isBuffer(payload)) {
    return payload;
  }

  if (payload instanceof Uint8Array) {
    return Buffer.from(payload);
  }

  return Buffer.from(payload);
}

function removeTrailingNulls(value) {
  return value.replace(/\0+$/g, '');
}

function deriveAesCbcPassword(ciphertextBase64, keyText, ivText = '1234567890123456') {
  if (typeof ciphertextBase64 !== 'string' || typeof keyText !== 'string') {
    return null;
  }

  const normalizedCiphertext = ciphertextBase64.trim();
  const normalizedKey = keyText.trim();

  if (!normalizedCiphertext || !normalizedKey) {
    return null;
  }

  try {
    const encrypted = Buffer.from(normalizedCiphertext, 'base64');
    const key = Buffer.from(normalizedKey, 'utf8');
    const iv = Buffer.from(ivText, 'utf8');

    if (![16, 24, 32].includes(key.length) || iv.length !== 16 || encrypted.length === 0) {
      return null;
    }

    const decipher = createDecipheriv('aes-' + key.length * 8 + '-cbc', key, iv);
    decipher.setAutoPadding(false);

    const decrypted = Buffer.concat([decipher.update(encrypted), decipher.final()]);
    const password = removeTrailingNulls(decrypted.toString('utf8'));

    return isLikelyPasswordValue(password) ? password : null;
  } catch {
    return null;
  }
}

function parseSegmentedPdfUrl(url) {
  try {
    const parsed = new URL(url);
    const match = parsed.pathname.match(/^\/pdf\/([^/]+)\/(\d+)_([^/.?#]+)\.pdf$/i);
    if (!match) {
      return null;
    }

    const [, bookToken, indexText, fileToken] = match;
    const index = Number(indexText);
    if (!Number.isInteger(index) || index < 0) {
      return null;
    }

    return {
      origin: parsed.origin,
      bookToken,
      fileToken,
      index,
      groupKey: parsed.origin + '|' + bookToken + '|' + fileToken,
      makeUrl(segmentIndex) {
        return new URL('/pdf/' + bookToken + '/' + segmentIndex + '_' + fileToken + '.pdf', parsed.origin).toString();
      }
    };
  } catch {
    return null;
  }
}

function findSegmentPaginationHint(urls, segmentInfo) {
  for (const rawUrl of urls) {
    if (typeof rawUrl !== 'string' || !rawUrl) {
      continue;
    }

    try {
      const parsed = new URL(rawUrl);
      const relatedUrls = [
        rawUrl,
        parsed.searchParams.get('url') ?? '',
        parsed.searchParams.get('file') ?? '',
        parsed.searchParams.get('src') ?? ''
      ];
      const matchesBook = relatedUrls.some((value) => value.includes('/pdf/' + segmentInfo.bookToken));
      if (!matchesBook) {
        continue;
      }

      const totalPages = Number(parsed.searchParams.get('total') ?? '');
      const pagesPerSegment = Number(parsed.searchParams.get('pagesize') ?? parsed.searchParams.get('pageSize') ?? '');
      if (!Number.isInteger(totalPages) || totalPages <= 0 || !Number.isInteger(pagesPerSegment) || pagesPerSegment <= 0) {
        continue;
      }

      return {
        totalPages,
        pagesPerSegment,
        segmentCount: Math.ceil(totalPages / pagesPerSegment),
        sourceUrl: rawUrl
      };
    } catch {
      // Ignore non-URL values.
    }
  }

  return null;
}

function buildSegmentPlan(selected, candidates, observedUrls) {
  const groups = new Map();
  const parsedSelected = parseSegmentedPdfUrl(selected.url);

  for (const candidate of [selected, ...candidates]) {
    const parsed = parseSegmentedPdfUrl(candidate.url);
    if (!parsed) {
      continue;
    }

    const existing = groups.get(parsed.groupKey) ?? {
      origin: parsed.origin,
      bookToken: parsed.bookToken,
      fileToken: parsed.fileToken,
      groupKey: parsed.groupKey,
      indices: new Set(),
      candidatesByIndex: new Map(),
      makeUrl: parsed.makeUrl
    };

    existing.indices.add(parsed.index);
    if (!existing.candidatesByIndex.has(parsed.index)) {
      existing.candidatesByIndex.set(parsed.index, candidate);
    }

    groups.set(parsed.groupKey, existing);
  }

  let targetGroup = null;
  if (parsedSelected) {
    targetGroup = groups.get(parsedSelected.groupKey) ?? null;
  } else if (groups.size === 1) {
    targetGroup = Array.from(groups.values())[0];
  }

  if (!targetGroup) {
    return null;
  }

  const discoveredIndices = Array.from(targetGroup.indices).sort((left, right) => left - right);
  const highestIndex = discoveredIndices.at(-1) ?? 0;
  const paginationHint = findSegmentPaginationHint([selected.url, ...candidates.map((candidate) => candidate.url), ...observedUrls], targetGroup);
  const segmentCount = Math.max(paginationHint?.segmentCount ?? 0, highestIndex + 1);
  if (segmentCount <= 1) {
    return null;
  }

  const probeIndex = parsedSelected && parsedSelected.groupKey === targetGroup.groupKey
    ? parsedSelected.index
    : discoveredIndices[0] ?? 0;

  return {
    bookToken: targetGroup.bookToken,
    fileToken: targetGroup.fileToken,
    groupKey: targetGroup.groupKey,
    totalPages: paginationHint?.totalPages ?? null,
    pagesPerSegment: paginationHint?.pagesPerSegment ?? null,
    segmentCount,
    discoveredIndices,
    paginationSourceUrl: paginationHint?.sourceUrl ?? '',
    probeIndex,
    probeUrl: targetGroup.makeUrl(probeIndex),
    makeUrl: targetGroup.makeUrl,
    indices: Array.from({ length: segmentCount }, (_, index) => index)
  };
}

async function downloadPdfBuffer(browserContext, ownerPage, url) {
  if (url.startsWith('blob:')) {
    const base64 = await ownerPage.evaluate(async (blobUrl) => {
      const response = await fetch(blobUrl);
      const blob = await response.blob();
      return await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(reader.error);
        reader.onloadend = () => {
          const result = String(reader.result ?? '');
          resolve(result.split(',')[1] ?? '');
        };
        reader.readAsDataURL(blob);
      });
    }, url);

    return Buffer.from(base64, 'base64');
  }

  if (url.startsWith('data:application/pdf;base64,')) {
    return Buffer.from(url.split(',')[1] ?? '', 'base64');
  }

  const response = await browserContext.request.get(url);
  if (!response.ok()) {
    throw new Error('Failed to download PDF: ' + response.status() + ' ' + response.statusText());
  }

  return normalizeBuffer(await response.body());
}

function forwardChildStream(stream, onLine) {
  if (!stream) {
    return Promise.resolve();
  }

  stream.setEncoding('utf8');

  return new Promise((resolve) => {
    const lineReader = createLineInterface({
      input: stream,
      crlfDelay: Infinity
    });

    lineReader.on('line', (line) => {
      const trimmed = line.trimEnd();
      if (trimmed) {
        onLine(trimmed);
      }
    });

    lineReader.once('close', resolve);
  });
}

async function runPythonBundleProcessor(args, ui) {
  if (ui.mode === 'cli') {
    await new Promise((resolve, reject) => {
      const child = spawn('python', ['scripts/process-pdf-bundle.py', ...args], {
        cwd: projectRoot,
        stdio: 'inherit'
      });

      child.once('error', reject);
      child.once('exit', (code) => {
        if (code === 0) {
          resolve();
          return;
        }

        reject(
          new Error(
            'Python PDF processor exited with code ' +
              code +
              '. Ensure its dependencies are installed with: python -m pip install pypdf'
          )
        );
      });
    });
    return;
  }

  await new Promise((resolve, reject) => {
    const child = spawn('python', ['scripts/process-pdf-bundle.py', ...args], {
      cwd: projectRoot,
      stdio: ['ignore', 'pipe', 'pipe']
    });

    const streamTasks = [
      forwardChildStream(child.stdout, (line) => {
        void ui.status(line, {
          source: 'process-pdf-bundle',
          stream: 'stdout'
        });
      }),
      forwardChildStream(child.stderr, (line) => {
        void ui.status(line, {
          source: 'process-pdf-bundle',
          stream: 'stderr'
        });
      })
    ];

    child.once('error', reject);
    child.once('exit', (code) => {
      Promise.allSettled(streamTasks).then(() => {
        if (code === 0) {
          resolve();
          return;
        }

        reject(
          new Error(
            'Python PDF processor exited with code ' +
              code +
              '. Ensure its dependencies are installed with: python -m pip install pypdf'
          )
        );
      });
    });
  });
}
async function extractTextFromPdf(buffer, password) {
  const loadingTask = pdfjsLib.getDocument({
    data: new Uint8Array(buffer),
    disableWorker: true,
    password
  });
  const document = await loadingTask.promise;
  const pages = [];

  for (let pageNumber = 1; pageNumber <= document.numPages; pageNumber += 1) {
    const page = await document.getPage(pageNumber);
    const textContent = await page.getTextContent();
    const text = textContent.items
      .map((item) => ('str' in item ? item.str : ''))
      .join(' ')
      .replace(/\s+/g, ' ')
      .trim();

    pages.push({
      pageNumber,
      text
    });
  }

  await document.destroy();

  return pages;
}

async function extractTextFromPdfWithPasswords(buffer, passwordCandidates) {
  const uniquePasswords = Array.from(new Set(passwordCandidates.filter(Boolean)));
  const attempts = [undefined, ...uniquePasswords];
  let lastPasswordError = null;

  for (const password of attempts) {
    try {
      const pages = await extractTextFromPdf(buffer, password);
      return {
        pages,
        passwordUsed: password ?? null
      };
    } catch (error) {
      if (looksLikePasswordError(error)) {
        lastPasswordError = error;
        continue;
      }

      throw error;
    }
  }

  throw lastPasswordError ?? new Error('No working PDF password was found.');
}

function serializeCandidate(candidate, index) {
  return {
    index,
    url: candidate.url,
    source: candidate.source,
    contentType: candidate.contentType ?? '',
    pageUrl: candidate.pageUrl ?? ''
  };
}

function createCliUi() {
  const rl = createInterface({
    input: process.stdin,
    output: process.stdout
  });

  return {
    mode: 'cli',
    async status(message) {
      console.log(message);
    },
    async waitForContinue({ message }) {
      await rl.question(`${message}\n`);
    },
    async chooseCandidate(candidates) {
      if (candidates.length === 1) {
        return candidates[0];
      }

      console.log('\nDetected multiple PDF candidates:');
      candidates.forEach((candidate, index) => {
        console.log(
          `${index + 1}. [${candidate.source}] ${truncate(candidate.url)}${candidate.contentType ? ` (${candidate.contentType})` : ''}`
        );
      });

      const answer = await rl.question('\nChoose a PDF number to download, or press Enter to use the first one: ');
      const selectedIndex = Number(answer.trim());

      if (Number.isInteger(selectedIndex) && selectedIndex >= 1 && selectedIndex <= candidates.length) {
        return candidates[selectedIndex - 1];
      }

      return candidates[0];
    },
    async close() {
      rl.close();
    }
  };
}

function createJsonBridgeUi() {
  const lineReader = createLineInterface({
    input: process.stdin,
    crlfDelay: Infinity
  });

  const queuedCommands = [];
  const waitingResolvers = [];
  let closed = false;

  const emit = (type, payload = {}) => {
    process.stdout.write(JSON.stringify({ type, ...payload }) + '\n');
  };

  const resolveNext = (command) => {
    const resolver = waitingResolvers.shift();
    if (resolver) {
      resolver.resolve(command);
      return;
    }

    queuedCommands.push(command);
  };

  lineReader.on('line', (line) => {
    const trimmed = line.trim();
    if (!trimmed) {
      return;
    }

    try {
      resolveNext(JSON.parse(trimmed));
    } catch {
      emit('status', {
        message: 'Ignored an invalid JSON bridge command.',
        source: 'bridge'
      });
    }
  });

  lineReader.on('close', () => {
    closed = true;
    while (waitingResolvers.length > 0) {
      waitingResolvers.shift().reject(new UserCancelledError('Desktop GUI closed the bridge input.'));
    }
  });

  const nextCommand = async () => {
    if (queuedCommands.length > 0) {
      return queuedCommands.shift();
    }

    if (closed) {
      throw new UserCancelledError('Desktop GUI closed the bridge input.');
    }

    return await new Promise((resolve, reject) => {
      waitingResolvers.push({ resolve, reject });
    });
  };

  return {
    mode: 'gui-bridge',
    async status(message, extra = {}) {
      emit('status', {
        message,
        ...extra
      });
    },
    async waitForContinue({ attempt, maxAttempts, message }) {
      emit('await_continue', {
        attempt,
        maxAttempts,
        message
      });

      while (true) {
        const command = await nextCommand();

        if (command?.type === 'cancel') {
          throw new UserCancelledError('Desktop GUI cancelled the download.');
        }

        if (command?.type === 'continue') {
          return;
        }

        emit('status', {
          message: 'Ignored an unexpected bridge command while waiting for continue.',
          source: 'bridge'
        });
      }
    },
    async chooseCandidate(candidates) {
      if (candidates.length === 1) {
        return candidates[0];
      }

      emit('choose_candidate', {
        candidates: candidates.map((candidate, index) => serializeCandidate(candidate, index))
      });

      while (true) {
        const command = await nextCommand();

        if (command?.type === 'cancel') {
          throw new UserCancelledError('Desktop GUI cancelled the download.');
        }

        if (command?.type === 'select_candidate') {
          const selectedIndex = Number(command.index);
          if (Number.isInteger(selectedIndex) && selectedIndex >= 0 && selectedIndex < candidates.length) {
            return candidates[selectedIndex];
          }
        }

        emit('status', {
          message: 'Ignored an invalid candidate selection command from the bridge.',
          source: 'bridge'
        });
      }
    },
    async downloadComplete(payload) {
      emit('download_complete', payload);
    },
    async error(message) {
      emit('error', { message });
    },
    async cancelled(message) {
      emit('cancelled', { message });
    },
    async close() {
      lineReader.close();
    }
  };
}

function createUi(options) {
  return options.guiBridge ? createJsonBridgeUi() : createCliUi();
}

function attachShutdownHandlers(closeBrowserContext) {
  const signals = ['SIGINT', 'SIGTERM', 'SIGBREAK'];
  const handlers = new Map();

  for (const signal of signals) {
    const handler = () => {
      void closeBrowserContext();
    };
    handlers.set(signal, handler);
    process.once(signal, handler);
  }

  return () => {
    for (const [signal, handler] of handlers) {
      process.removeListener(signal, handler);
    }
  };
}

function collectPageHints() {
  const selectors = [
    ['embed', 'src'],
    ['iframe', 'src'],
    ['object', 'data'],
    ['a', 'href']
  ];
  const results = [];

  for (const [selector, attribute] of selectors) {
    for (const element of document.querySelectorAll(`${selector}[${attribute}]`)) {
      const raw = element.getAttribute(attribute);
      if (!raw) {
        continue;
      }

      results.push({
        source: `${selector}-${attribute}`,
        rawUrl: raw
      });
    }
  }

  const viewerUrl =
    window.PDFViewerApplication?.url ||
    window.PDFViewerApplicationOptions?.get?.('defaultUrl') ||
    null;

  if (viewerUrl) {
    results.push({
      source: 'pdfjs-viewer',
      rawUrl: viewerUrl
    });
  }

  return results;
}

async function installPasswordSniffer(browserContext) {
  await browserContext.addInitScript(() => {
    const keyPattern = /(pass(?:word)?|pwd|passwd|passphrase|openpwd|pdfpwd)/i;
    const store = {
      passwords: []
    };
    const seenPasswords = new Set();

    const addPassword = (value, source) => {
      if (typeof value !== 'string') {
        return;
      }

      const trimmed = value.trim();
      if (!trimmed || trimmed.length > 128) {
        return;
      }

      if (/^https?:/i.test(trimmed) || /^blob:/i.test(trimmed) || /^data:/i.test(trimmed)) {
        return;
      }

      if ((trimmed.startsWith('{') || trimmed.startsWith('[') || trimmed.startsWith('<')) && trimmed.length > 20) {
        return;
      }

      const key = `${source}|${trimmed}`;
      if (seenPasswords.has(key)) {
        return;
      }

      seenPasswords.add(key);
      store.passwords.push({
        value: trimmed,
        source
      });
    };

    const inspectString = (text, source) => {
      if (typeof text !== 'string' || !text) {
        return;
      }

      const patterns = [
        /["'](?:pass(?:word)?|pwd|passwd|passphrase|openpwd|pdfpwd)["']\s*:\s*["']([^"'\\]+)["']/gi,
        /(?:pass(?:word)?|pwd|passwd|passphrase|openpwd|pdfpwd)\s*[=:]\s*["']?([^"'&,\s}]+)/gi
      ];

      for (const pattern of patterns) {
        let match = pattern.exec(text);
        while (match) {
          addPassword(match[1], `${source}:pattern`);
          match = pattern.exec(text);
        }
      }
    };

    const inspectValue = (value, source, depth = 0, objectSeen = new WeakSet()) => {
      if (depth > 5 || value == null) {
        return;
      }

      if (typeof value === 'string') {
        inspectString(value, source);
        return;
      }

      if (typeof value === 'number' || typeof value === 'boolean') {
        return;
      }

      if (typeof value !== 'object') {
        return;
      }

      if (objectSeen.has(value)) {
        return;
      }

      objectSeen.add(value);

      if (typeof FormData !== 'undefined' && value instanceof FormData) {
        for (const [key, nested] of value.entries()) {
          if (keyPattern.test(String(key))) {
            addPassword(String(nested), `${source}:${String(key)}`);
          }
        }
        return;
      }

      if (typeof URLSearchParams !== 'undefined' && value instanceof URLSearchParams) {
        for (const [key, nested] of value.entries()) {
          if (keyPattern.test(String(key))) {
            addPassword(String(nested), `${source}:${String(key)}`);
          }
        }
        inspectString(value.toString(), source);
        return;
      }

      if (Array.isArray(value)) {
        for (const nested of value.slice(0, 25)) {
          inspectValue(nested, `${source}[]`, depth + 1, objectSeen);
        }
        return;
      }

      const entries = Object.entries(value).slice(0, 50);
      for (const [key, nested] of entries) {
        if (keyPattern.test(String(key))) {
          if (typeof nested === 'string' || typeof nested === 'number' || typeof nested === 'boolean') {
            addPassword(String(nested), `${source}:${String(key)}`);
          } else {
            inspectValue(nested, `${source}:${String(key)}`, depth + 1, objectSeen);
          }
          continue;
        }

        if (depth < 2) {
          inspectValue(nested, `${source}.${String(key)}`, depth + 1, objectSeen);
        }
      }
    };

    const inspectKnownGlobals = () => {
      const names = ['bookData', 'readerData', 'pdfData', 'pdfOptions', '__INITIAL_STATE__', 'pageData'];
      for (const name of names) {
        try {
          inspectValue(window[name], `global:${name}`);
        } catch {
          // Ignore access errors.
        }
      }
    };

    const wrapGetDocument = (holder, source) => {
      if (!holder || typeof holder.getDocument !== 'function' || holder.getDocument.__pdfGrabberWrapped) {
        return;
      }

      const original = holder.getDocument.bind(holder);
      const wrapped = function (src, ...rest) {
        try {
          inspectValue(src, `${source}:getDocument`);
        } catch {
          // Ignore interception errors.
        }
        return original(src, ...rest);
      };

      wrapped.__pdfGrabberWrapped = true;
      holder.getDocument = wrapped;
    };

    const probePdfjs = () => {
      try {
        wrapGetDocument(window.pdfjsLib, 'window.pdfjsLib');
      } catch {
        // Ignore access errors.
      }

      try {
        wrapGetDocument(window.PDFViewerApplication?.pdfjsLib, 'PDFViewerApplication.pdfjsLib');
      } catch {
        // Ignore access errors.
      }
    };

    window.__pdfGrabber = store;

    window.addEventListener('message', (event) => {
      try {
        inspectValue(event.data, `message:${event.origin}`);
      } catch {
        // Ignore message parsing errors.
      }
    });

    if (typeof window.fetch === 'function') {
      const originalFetch = window.fetch.bind(window);
      window.fetch = async (...args) => {
        try {
          inspectValue(args[1]?.body, `fetch-request:${typeof args[0] === 'string' ? args[0] : args[0]?.url ?? ''}`);
        } catch {
          // Ignore request parsing errors.
        }

        const response = await originalFetch(...args);

        try {
          const contentType = response.headers.get('content-type') || '';
          if (contentType.includes('json') || contentType.includes('text') || contentType.includes('javascript')) {
            response
              .clone()
              .text()
              .then((text) => inspectString(text, `fetch-response:${response.url}`))
              .catch(() => {});
          }
        } catch {
          // Ignore response parsing errors.
        }

        return response;
      };
    }

    const originalOpen = XMLHttpRequest.prototype.open;
    const originalSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function (method, url, ...rest) {
      this.__pdfGrabberUrl = String(url ?? '');
      return originalOpen.call(this, method, url, ...rest);
    };

    XMLHttpRequest.prototype.send = function (body) {
      try {
        inspectValue(body, `xhr-request:${this.__pdfGrabberUrl ?? ''}`);
      } catch {
        // Ignore request parsing errors.
      }

      this.addEventListener(
        'load',
        () => {
          try {
            const contentType = this.getResponseHeader('content-type') || '';
            if (typeof this.responseText === 'string' && (contentType.includes('json') || contentType.includes('text') || contentType.includes('javascript'))) {
              inspectString(this.responseText, `xhr-response:${this.responseURL || this.__pdfGrabberUrl || ''}`);
            }
          } catch {
            // Ignore response parsing errors.
          }
        },
        { once: true }
      );

      return originalSend.call(this, body);
    };

    const wrapStorage = (storage, name) => {
      if (!storage || typeof storage.setItem !== 'function') {
        return;
      }

      const originalSetItem = storage.setItem.bind(storage);
      storage.setItem = (key, value) => {
        try {
          if (keyPattern.test(String(key))) {
            addPassword(String(value), `${name}:${String(key)}`);
          }
          inspectString(String(value), `${name}:${String(key)}`);
        } catch {
          // Ignore storage parsing errors.
        }

        return originalSetItem(key, value);
      };
    };

    try {
      wrapStorage(window.localStorage, 'localStorage');
    } catch {
      // Ignore storage access errors.
    }

    try {
      wrapStorage(window.sessionStorage, 'sessionStorage');
    } catch {
      // Ignore storage access errors.
    }

    inspectKnownGlobals();
    probePdfjs();

    const timerId = window.setInterval(() => {
      inspectKnownGlobals();
      probePdfjs();
    }, 1000);

    window.setTimeout(() => window.clearInterval(timerId), 60000);
  });
}

async function collectDetectedPasswordsFromPage(page, pageId, registerPassword) {
  for (const frame of page.frames()) {
    const detected = await frame.evaluate(() => window.__pdfGrabber?.passwords ?? []).catch(() => []);

    for (const entry of detected) {
      registerPassword({
        value: entry?.value,
        source: entry?.source,
        pageId,
        pageUrl: page.url(),
        frameUrl: frame.url()
      });
    }
  }
}

async function collectDerivedPasswordsFromPage(page, pageId, registerPassword) {
  for (const frame of page.frames()) {
    const keyPairs = await frame
      .evaluate(() => {
        const results = [];
        const seen = new Set();

        const addPair = (openKey, uKey, source, isDecrypt) => {
          const normalizedOpenKey = typeof openKey === 'string' ? openKey.trim() : '';
          const normalizedUKey = typeof uKey === 'string' ? uKey.trim() : '';

          if (!normalizedOpenKey || !normalizedUKey) {
            return;
          }

          const signature = source + '|' + normalizedOpenKey + '|' + normalizedUKey;
          if (seen.has(signature)) {
            return;
          }

          seen.add(signature);
          results.push({
            openKey: normalizedOpenKey,
            uKey: normalizedUKey,
            source,
            isDecrypt: Boolean(isDecrypt)
          });
        };

        const inspectWindow = (target, label) => {
          if (!target) {
            return;
          }

          try {
            const awtConfig = target.awtConfig;
            addPair(target.openKey, awtConfig?.uKey ?? target.uKey, label + ':openKey+uKey', awtConfig?.isDecrypt);
            addPair(target.openKey ?? awtConfig?.openKey, awtConfig?.uKey, label + ':awtConfig', awtConfig?.isDecrypt);
          } catch {
            // Ignore cross-origin access errors.
          }
        };

        const inspectStorage = (storage, label) => {
          if (!storage || typeof storage.getItem !== 'function') {
            return;
          }

          try {
            addPair(storage.getItem('openKey'), storage.getItem('uKey'), label + ':direct', false);
            addPair(storage.getItem('openkey'), storage.getItem('ukey'), label + ':lowercase', false);
          } catch {
            // Ignore storage access errors.
          }

          const openKeyByName = new Map();
          const uKeyByName = new Map();

          for (let index = 0; index < Math.min(storage.length, 100); index += 1) {
            try {
              const key = storage.key(index);
              if (!key) {
                continue;
              }

              const lowerKey = String(key).toLowerCase();
              const value = storage.getItem(key);

              if (!value) {
                continue;
              }

              if (lowerKey.includes('openkey')) {
                openKeyByName.set(key, value);
              }

              if (lowerKey.includes('ukey')) {
                uKeyByName.set(key, value);
              }
            } catch {
              // Ignore storage parsing errors.
            }
          }

          for (const [openKeyName, openKeyValue] of openKeyByName) {
            for (const [uKeyName, uKeyValue] of uKeyByName) {
              addPair(openKeyValue, uKeyValue, label + ':' + openKeyName + '+' + uKeyName, false);
            }
          }
        };

        inspectWindow(window, 'window');

        try {
          if (window.parent && window.parent !== window) {
            inspectWindow(window.parent, 'parent');
          }
        } catch {
          // Ignore cross-origin access errors.
        }

        try {
          if (window.top && window.top !== window) {
            inspectWindow(window.top, 'top');
          }
        } catch {
          // Ignore cross-origin access errors.
        }

        try {
          inspectStorage(window.localStorage, 'localStorage');
        } catch {
          // Ignore storage access errors.
        }

        try {
          inspectStorage(window.sessionStorage, 'sessionStorage');
        } catch {
          // Ignore storage access errors.
        }

        return results;
      })
      .catch(() => []);

    for (const entry of keyPairs) {
      const password = deriveAesCbcPassword(entry?.openKey, entry?.uKey);
      if (!password) {
        continue;
      }

      registerPassword({
        value: password,
        source: entry.source + (entry.isDecrypt ? ':decrypt-enabled' : '') + ':derived',
        pageId,
        pageUrl: page.url(),
        frameUrl: frame.url()
      });
    }
  }
}

async function runGrabPdf(rawOptions, ui) {
  const options = normalizeOptions(rawOptions);

  await mkdir(options.outputDir, { recursive: true });
  await mkdir(options.profileDir, { recursive: true });

  const browserContext = await chromium.launchPersistentContext(options.profileDir, {
    executablePath: options.browserPath,
    headless: false,
    acceptDownloads: true,
    viewport: null
  });

  let browserClosed = false;
  const closeBrowserContext = async () => {
    if (browserClosed) {
      return;
    }

    browserClosed = true;
    await browserContext.close().catch(() => {});
  };
  const detachShutdownHandlers = attachShutdownHandlers(closeBrowserContext);

  await installPasswordSniffer(browserContext);

  const pageIds = new Map();
  const trackedPages = new Set();
  const candidates = new Map();
  const detectedPasswords = new Map();
  const observedUrls = new Set();

  let pageCounter = 0;

  const registerCandidate = (candidate) => {
    if (!candidate?.url) {
      return;
    }

    const key = `${candidate.pageId}|${candidate.source}|${candidate.url}`;
    const existing = candidates.get(key);

    if (existing) {
      existing.seen += 1;
      if (!existing.contentType && candidate.contentType) {
        existing.contentType = candidate.contentType;
      }
      return;
    }

    candidates.set(key, {
      ...candidate,
      seen: 1
    });
  };

  const registerPassword = (entry) => {
    const value = typeof entry?.value === 'string' ? entry.value.trim() : '';
    if (!isLikelyPasswordValue(value)) {
      return;
    }

    const existing = detectedPasswords.get(value);
    if (existing) {
      existing.seen += 1;
      if (entry.source && !existing.sources.includes(entry.source)) {
        existing.sources.push(entry.source);
      }
      return;
    }

    detectedPasswords.set(value, {
      value,
      seen: 1,
      sources: entry.source ? [entry.source] : [],
      pageId: entry.pageId ?? '',
      pageUrl: entry.pageUrl ?? '',
      frameUrl: entry.frameUrl ?? ''
    });
  };

  const attachToPage = (page) => {
    if (trackedPages.has(page)) {
      return;
    }

    trackedPages.add(page);
    const pageId = `page-${++pageCounter}`;
    pageIds.set(page, pageId);

    page.on('response', async (response) => {
      try {
        const url = response.url();
        observedUrls.add(url);
        const headers = await response.allHeaders();
        const contentType = headers['content-type'] ?? '';

        if (contentType.includes('pdf') || /\.pdf(\?|$)/i.test(url)) {
          registerCandidate({
            pageId,
            pageUrl: page.url(),
            source: 'network-response',
            url,
            contentType
          });
        }

        const nestedUrl = extractViewerFile(url);
        if (nestedUrl) {
          registerCandidate({
            pageId,
            pageUrl: page.url(),
            source: 'viewer-file-param',
            url: nestedUrl,
            contentType
          });
        }
      } catch {
        // Ignore transient response errors from closed pages.
      }
    });
  };

  browserContext.pages().forEach(attachToPage);
  browserContext.on('page', attachToPage);

  try {
    const page = browserContext.pages()[0] ?? (await browserContext.newPage());
    page.setDefaultNavigationTimeout(options.timeoutMs);

    if (options.url) {
      await ui.status(`Opening ${options.url}`);
      await page.goto(options.url, { waitUntil: 'domcontentloaded' });
    } else {
      await page.goto('about:blank');
    }

    await ui.status('\nA browser window is open.');
    await ui.status('Log in, navigate to the page that shows the PDF, and open the PDF itself if there is a button or link.');

    let rankedCandidates = [];

    for (let attempt = 1; attempt <= options.maxAttempts; attempt += 1) {
      await ui.waitForContinue({
        attempt,
        maxAttempts: options.maxAttempts,
        message: `Attempt ${attempt}/${options.maxAttempts}: press Enter here after the PDF page is fully open in the browser.`
      });

      await page.waitForTimeout(options.waitAfterLoginMs);

      for (const currentPage of browserContext.pages()) {
        const pageId = pageIds.get(currentPage);

        if (!pageId || currentPage.isClosed()) {
          continue;
        }

        observedUrls.add(currentPage.url());
        for (const frame of currentPage.frames()) {
          observedUrls.add(frame.url());
        }

        await collectDetectedPasswordsFromPage(currentPage, pageId, registerPassword);
        await collectDerivedPasswordsFromPage(currentPage, pageId, registerPassword);

        const rawHints = await currentPage.evaluate(collectPageHints).catch(() => []);

        for (const hint of rawHints) {
          const normalizedUrl = safeUrl(hint.rawUrl, currentPage.url());
          if (!normalizedUrl) {
            continue;
          }

          observedUrls.add(normalizedUrl);

          if (looksLikePdfUrl(normalizedUrl)) {
            registerCandidate({
              pageId,
              pageUrl: currentPage.url(),
              source: hint.source,
              url: normalizedUrl,
              contentType: ''
            });
          }

          const nestedUrl = extractViewerFile(normalizedUrl);
          if (nestedUrl) {
            observedUrls.add(nestedUrl);
            registerCandidate({
              pageId,
              pageUrl: currentPage.url(),
              source: 'viewer-file-param',
              url: nestedUrl,
              contentType: ''
            });
          }
        }
      }

      rankedCandidates = Array.from(candidates.values())
        .filter((candidate) => (candidate.contentType ?? '').includes('pdf') || looksLikePdfUrl(candidate.url))
        .filter((candidate) => !options.match || candidate.url.toLowerCase().includes(options.match.toLowerCase()))
        .sort((left, right) => scoreCandidate(right, options.match) - scoreCandidate(left, options.match));

      if (rankedCandidates.length > 0) {
        break;
      }

      await ui.status('No PDF candidate was detected yet. Open the PDF in the browser and press Enter again.');
    }

    if (rankedCandidates.length === 0) {
      throw new Error('No PDF candidate was found. Try increasing --max-attempts, waiting longer, or opening the PDF viewer before pressing Enter.');
    }

    const selected = await ui.chooseCandidate(rankedCandidates);
    const ownerPage = browserContext.pages().find((item) => pageIds.get(item) === selected.pageId);

    if (!ownerPage) {
      throw new Error('The browser page that exposed the PDF is no longer open.');
    }

    const passwordCandidates = [
      options.pdfPassword,
      ...Array.from(detectedPasswords.values())
        .sort((left, right) => right.seen - left.seen)
        .map((item) => item.value)
    ].filter(Boolean);

    if (passwordCandidates.length > 0) {
      await ui.status('Detected ' + passwordCandidates.length + ' PDF password candidate(s).');
    }

    const segmentPlan = options.singleSegment ? null : buildSegmentPlan(selected, rankedCandidates, observedUrls);
    const downloadUrl = segmentPlan?.probeUrl ?? selected.url;

    await ui.status('\nDownloading PDF from: ' + downloadUrl);
    const pdfBuffer = await downloadPdfBuffer(browserContext, ownerPage, downloadUrl);

    if (!pdfBuffer || pdfBuffer.length === 0) {
      throw new Error('Downloaded PDF is empty.');
    }

    let extractionResult;

    try {
      extractionResult = await extractTextFromPdfWithPasswords(pdfBuffer, passwordCandidates);
    } catch (error) {
      if (looksLikePasswordError(error)) {
        if (passwordCandidates.length > 0) {
          throw new Error('A PDF password is required, but none of the detected candidates worked. Try rerunning with --pdf-password if the site uses a different password.');
        }

        throw new Error('A PDF password is required, but no password was detected from the page. Open the PDF in the browser before pressing Enter, or rerun with --pdf-password.');
      }

      throw error;
    }

    const passwordUsed = extractionResult.passwordUsed;

    if (segmentPlan) {
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
      const bundleStem = sanitizeFilename(segmentPlan.bookToken, 'captured-pdf');
      const bundleDir = path.join(options.outputDir, bundleStem + '-' + timestamp);
      const segmentsDir = path.join(bundleDir, 'segments');
      const mergedPdfPath = path.join(bundleDir, bundleStem + '-merged.pdf');
      const processingMetadataPath = path.join(bundleDir, bundleStem + '-processing.json');
      const manifestPath = path.join(bundleDir, bundleStem + '-download-manifest.json');

      await mkdir(segmentsDir, { recursive: true });

      await ui.status('\nDetected segmented PDF set: ' + segmentPlan.segmentCount + ' file(s)' + (segmentPlan.totalPages ? ' covering about ' + segmentPlan.totalPages + ' page(s).' : '.'));

      const savedSegments = [];
      for (const index of segmentPlan.indices) {
        const segmentUrl = segmentPlan.makeUrl(index);
        await ui.status('Downloading segment ' + (index + 1) + '/' + segmentPlan.segmentCount + ': ' + segmentUrl);

        const segmentBuffer = segmentUrl === downloadUrl
          ? pdfBuffer
          : await downloadPdfBuffer(browserContext, ownerPage, segmentUrl);

        if (!segmentBuffer || segmentBuffer.length === 0) {
          throw new Error('Downloaded PDF segment is empty: ' + segmentUrl);
        }

        const segmentPath = path.join(
          segmentsDir,
          String(index).padStart(3, '0') + '-' + path.basename(new URL(segmentUrl).pathname)
        );

        await writeFile(segmentPath, segmentBuffer);
        savedSegments.push({
          index,
          url: segmentUrl,
          pdfPath: segmentPath,
          byteLength: segmentBuffer.length
        });
      }

      await writeFile(
        manifestPath,
        JSON.stringify(
          {
            sourceSelection: {
              url: selected.url,
              pageUrl: selected.pageUrl,
              source: selected.source,
              contentType: selected.contentType
            },
            segmentPlan: {
              bookToken: segmentPlan.bookToken,
              fileToken: segmentPlan.fileToken,
              segmentCount: segmentPlan.segmentCount,
              totalPages: segmentPlan.totalPages,
              pagesPerSegment: segmentPlan.pagesPerSegment,
              discoveredIndices: segmentPlan.discoveredIndices,
              paginationSourceUrl: segmentPlan.paginationSourceUrl
            },
            passwordUsed,
            detectedPasswords: Array.from(detectedPasswords.values()),
            candidates: rankedCandidates,
            segments: savedSegments
          },
          null,
          2
        ),
        'utf8'
      );

      await ui.status('\nMerging segments...');

      const processorArgs = [
        '--output-pdf',
        mergedPdfPath,
        '--output-metadata',
        processingMetadataPath
      ];

      if (passwordUsed) {
        processorArgs.push('--password', passwordUsed);
      }
      processorArgs.push(...savedSegments.map((segment) => segment.pdfPath));
      await runPythonBundleProcessor(processorArgs, ui);

      await ui.status('\nDone.');
      if (passwordUsed) {
        await ui.status('PDF password: ' + passwordUsed);
      }
      await ui.status('Segments saved to: ' + segmentsDir);
      await ui.status('Merged PDF saved to: ' + mergedPdfPath);
      await ui.status('Download manifest saved to: ' + manifestPath);
      await ui.status('Processing metadata saved to: ' + processingMetadataPath);
      return {
        sourcePdfPath: mergedPdfPath,
        merged: true,
        manifestPath,
        processingMetadataPath,
        passwordUsed
      };
    }

    const parsedUrl = !downloadUrl.startsWith('blob:') && !downloadUrl.startsWith('data:') ? new URL(downloadUrl) : null;
    const urlFilename = parsedUrl ? path.basename(parsedUrl.pathname) : '';
    const stem = sanitizeFilename(path.parse(urlFilename || 'captured-pdf.pdf').name, 'captured-pdf');
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const pdfPath = path.join(options.outputDir, `${stem}-${timestamp}.pdf`);
    const metadataPath = path.join(options.outputDir, `${stem}-${timestamp}.json`);

    await writeFile(pdfPath, pdfBuffer);

    const { pages } = extractionResult;
    await writeFile(
      metadataPath,
      JSON.stringify(
        {
          sourceUrl: downloadUrl,
          sourcePage: selected.pageUrl,
          sourceType: selected.source,
          contentType: selected.contentType,
          pdfPath,
          pageCount: pages.length,
          passwordUsed,
          detectedPasswords: Array.from(detectedPasswords.values()),
          candidates: rankedCandidates
        },
        null,
        2
      ),
      'utf8'
    );

    await ui.status('\nDone.');
    if (passwordUsed) {
      await ui.status(`PDF password: ${passwordUsed}`);
    }
    await ui.status(`PDF saved to: ${pdfPath}`);
    await ui.status(`Metadata saved to: ${metadataPath}`);
    return {
      sourcePdfPath: pdfPath,
      merged: false,
      manifestPath: metadataPath,
      processingMetadataPath: null,
      passwordUsed
    };
  } finally {
    detachShutdownHandlers();
    await closeBrowserContext();
  }
}

async function runCliEntry(argv = process.argv.slice(2)) {
  let options;
  try {
    options = parseArgs(argv);
  } catch (error) {
    console.error(`\nError: ${error.message}`);
    return 1;
  }

  if (options.showHelp) {
    printHelp();
    return 0;
  }

  const ui = createUi(options);

  try {
    const result = await runGrabPdf(options, ui);
    if (options.guiBridge) {
      await ui.downloadComplete(result);
    }
    return 0;
  } catch (error) {
    if (error instanceof UserCancelledError) {
      if (options.guiBridge) {
        await ui.cancelled(error.message);
      } else {
        console.error('\nCancelled.');
      }
      return 130;
    }

    if (options.guiBridge) {
      await ui.error(error.message);
    } else {
      console.error(`\nError: ${error.message}`);
    }
    return 1;
  } finally {
    await ui.close();
  }
}

export { runGrabPdf, runCliEntry };

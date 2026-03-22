from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import fitz


ROOT_DIR = Path(__file__).resolve().parent
DOWNLOADER_DIR = ROOT_DIR / 'dl-renwei-ebook'
DOWNLOADER_ENTRY = DOWNLOADER_DIR / 'grab-pdf-cli.mjs'
REPAIR_DIR = ROOT_DIR / 'pdf_fixer'
REPAIR_ENTRY = REPAIR_DIR / 'repair_pdf_no_ocr.py'
SETTINGS_PATH = ROOT_DIR / '.renwei_gui_settings.json'
DEFAULT_BROWSER_PATH = os.environ.get(
    'PLAYWRIGHT_CHROME_PATH',
    r'C:\Users\ORR\AppData\Local\Google\Chrome\Application\chrome.exe',
)
TITLE_PAGE_NUMBER = 2
SAMPLED_PAGE_NUMBERS = (1, 6, 20)
GARBLED_RANGES = (
    (0x0370, 0x03FF),
    (0x0400, 0x04FF),
    (0x0900, 0x109F),
    (0x1780, 0x17FF),
)
EDITION_PATTERN = re.compile(r'第\s*[0-9一二三四五六七八九十百零〇]+\s*版')
GENERIC_TITLE_MARKERS = (
    '国家卫生健康委员会',
    '规划教材',
    '十四五',
    '全国高等学校',
    '数字教材',
    '主编',
    '副主编',
    '编者',
    '出版社',
    'isbn',
    'cip',
    '供',
)
AUTHOR_LINE_MARKERS = (
    '主编',
    '副主编',
    '编者',
    '主审',
    '审校',
    '作者',
)


def open_path(path_str: str) -> None:
    if not path_str:
        return
    path = Path(path_str)
    if not path.exists():
        messagebox.showerror('路径不存在', f'找不到路径:\n{path}')
        return
    if hasattr(os, 'startfile'):
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    subprocess.Popen(['xdg-open', str(path)])


def start_pipe_reader(pipe, event_queue: queue.Queue, event_name: str, parse_json: bool = False) -> None:
    def worker() -> None:
        try:
            with pipe:
                for raw_line in pipe:
                    line = raw_line.rstrip('\r\n')
                    if not line:
                        continue
                    if parse_json:
                        try:
                            payload = json.loads(line)
                            event_queue.put((event_name, payload))
                        except json.JSONDecodeError:
                            event_queue.put(('log', f'[bridge] {line}'))
                    else:
                        event_queue.put((event_name, line))
        except Exception as exc:
            event_queue.put(('log', f'读取子进程输出失败: {exc}'))

    threading.Thread(target=worker, daemon=True).start()


def contains_cjk(text: str) -> bool:
    return any(
        ('\u3400' <= ch <= '\u4dbf')
        or ('\u4e00' <= ch <= '\u9fff')
        or ('\uf900' <= ch <= '\ufaff')
        for ch in text
    )


def normalize_inline_text(text: str) -> str:
    compact = re.sub(r'\s+', '', text or '')
    compact = compact.replace('\u3000', '')
    return compact.strip()


def looks_garbled_text(text: str) -> bool:
    normalized = normalize_inline_text(text)
    if not normalized:
        return False

    interesting = 0
    garbled = 0
    cjk_count = 0
    replacement_count = 0

    for ch in normalized:
        if ch.isspace():
            continue
        if ch in {'�', '□'}:
            interesting += 1
            garbled += 1
            replacement_count += 1
            continue
        if contains_cjk(ch):
            cjk_count += 1
        if ch.isascii():
            continue
        interesting += 1
        cp = ord(ch)
        for start, end in GARBLED_RANGES:
            if start <= cp <= end:
                garbled += 1
                break

    if garbled >= 4 and cjk_count == 0:
        return True
    if interesting < 6:
        return replacement_count >= 2
    if garbled / interesting >= 0.35:
        return True
    return cjk_count == 0 and garbled >= 4


def sanitize_filename_stem(value: str) -> str:
    cleaned = normalize_inline_text(value)
    cleaned = re.sub(r'[<>:"/\\|?*]+', '-', cleaned)
    cleaned = re.sub(r'-{2,}', '-', cleaned).strip(' .-_')
    return cleaned[:120]


def make_unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f'{path.stem}-{counter}{path.suffix}')
        if not candidate.exists():
            return candidate
        counter += 1


def path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except Exception:
        return False
    return True


def open_pdf_document(pdf_path: Path, password: str = '') -> fitz.Document:
    document = fitz.open(str(pdf_path))
    if document.needs_pass:
        if not password or document.authenticate(password) <= 0:
            document.close()
            raise RuntimeError(f'PDF 需要密码才能读取: {pdf_path.name}')
    return document


def collect_page_samples(pdf_path: Path, password: str = '') -> tuple[list[int], list[str]]:
    checked_pages: list[int] = []
    garbled_pages: list[str] = []
    document = open_pdf_document(pdf_path, password)
    try:
        for page_number in SAMPLED_PAGE_NUMBERS:
            if page_number > document.page_count:
                continue
            checked_pages.append(page_number)
            text = document.load_page(page_number - 1).get_text('text')
            preview = normalize_inline_text(text)[:80]
            if looks_garbled_text(text):
                garbled_pages.append(f'{page_number}({preview or "无可提取文本"})')
    finally:
        document.close()
    return checked_pages, garbled_pages


def extract_title_lines(pdf_path: Path, password: str = '') -> list[dict]:
    document = open_pdf_document(pdf_path, password)
    try:
        page_index = TITLE_PAGE_NUMBER - 1 if document.page_count >= TITLE_PAGE_NUMBER else 0
        page = document.load_page(page_index)
        text_dict = page.get_text('dict')
    finally:
        document.close()

    lines: list[dict] = []
    for block in text_dict.get('blocks', []):
        if block.get('type') != 0:
            continue
        for line in block.get('lines', []):
            spans = [span for span in line.get('spans', []) if normalize_inline_text(span.get('text', ''))]
            if not spans:
                continue
            text = normalize_inline_text(''.join(span.get('text', '') for span in spans))
            if not text:
                continue
            font_size = max(float(span.get('size', 0.0)) for span in spans)
            y_position = min(float(span.get('bbox', [0, 0, 0, 0])[1]) for span in spans)
            lines.append(
                {
                    'text': text,
                    'font_size': font_size,
                    'x': min(float(span.get('bbox', [0, 0, 0, 0])[0]) for span in spans),
                    'y': y_position,
                }
            )

    lines.sort(key=lambda item: (item['y'], -item['font_size']))
    deduped: list[dict] = []
    seen: set[str] = set()
    for line in lines:
        if line['text'] in seen:
            continue
        seen.add(line['text'])
        deduped.append(line)
    return deduped


def is_generic_title_line(text: str) -> bool:
    lower_text = text.lower()
    return any(marker.lower() in lower_text for marker in GENERIC_TITLE_MARKERS)


def is_author_line(text: str) -> bool:
    normalized = normalize_inline_text(text)
    return any(marker in normalized for marker in AUTHOR_LINE_MARKERS)


def has_title_and_edition(text: str) -> bool:
    if not EDITION_PATTERN.search(text):
        return False
    remainder = EDITION_PATTERN.sub('', text)
    return contains_cjk(remainder)


def is_probable_title_line(text: str) -> bool:
    normalized = normalize_inline_text(text)
    if not normalized:
        return False
    if is_generic_title_line(normalized) or is_author_line(normalized):
        return False
    if EDITION_PATTERN.fullmatch(normalized):
        return False
    if not contains_cjk(normalized):
        return False
    if len(normalized) < 2 or len(normalized) > 24:
        return False
    return True


def extract_edition_from_lines(lines: list[dict], title_line: dict) -> str:
    lower_bound = title_line['y']
    upper_bound = title_line['y'] + 240
    nearby = [
        line for line in lines
        if lower_bound <= line['y'] <= upper_bound and line['font_size'] <= title_line['font_size']
    ]
    nearby.sort(key=lambda item: (item['y'], item.get('x', 0.0)))

    for line in nearby:
        match = EDITION_PATTERN.search(line['text'])
        if match:
            return normalize_inline_text(match.group(0))

    edition_start_index = None
    for index, line in enumerate(nearby):
        text = normalize_inline_text(line['text'])
        if re.fullmatch(r'第\s*[0-9一二三四五六七八九十百零〇]+', text):
            edition_start_index = index
            break

    if edition_start_index is not None:
        start_text = normalize_inline_text(nearby[edition_start_index]['text'])
        combined = start_text
        for line in nearby[edition_start_index + 1: edition_start_index + 4]:
            fragment = normalize_inline_text(line['text'])
            if fragment in {'版', '修订版'} or fragment.endswith('版'):
                combined += fragment
                match = EDITION_PATTERN.search(combined)
                if match:
                    return normalize_inline_text(match.group(0))
                break

    for line in nearby:
        text = normalize_inline_text(line['text'])
        if text.endswith('版') and EDITION_PATTERN.search(text):
            return normalize_inline_text(EDITION_PATTERN.search(text).group(0))

    return ''


def extract_book_title(pdf_path: Path, password: str = '') -> str:
    lines = extract_title_lines(pdf_path, password)
    if not lines:
        return ''

    top_size = max(line['font_size'] for line in lines)
    title_candidates = [
        line for line in lines
        if is_probable_title_line(line['text']) and line['font_size'] >= max(18.0, top_size * 0.72)
    ]
    if not title_candidates:
        title_candidates = [
            line for line in lines
            if is_probable_title_line(line['text'])
        ]
    if not title_candidates:
        return ''

    title_line = max(title_candidates, key=lambda item: (item['font_size'], -item['y'], -item.get('x', 0.0)))
    title_text = normalize_inline_text(title_line['text'])

    if has_title_and_edition(title_text):
        return title_text

    edition_text = extract_edition_from_lines(lines, title_line)
    if edition_text:
        return title_text + edition_text

    return title_text


class CandidateDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, candidates: list[dict], on_confirm, on_cancel) -> None:
        super().__init__(master)
        self.title('选择 PDF')
        self.resizable(True, True)
        self.geometry('860x360')
        self._candidates = candidates
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel

        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(container, text='检测到多个 PDF 候选，请选择要下载的项目。').pack(anchor=tk.W)

        self.listbox = tk.Listbox(container, activestyle='dotbox')
        self.listbox.pack(fill=tk.BOTH, expand=True, pady=(8, 10))
        for candidate in candidates:
            preview = candidate['url']
            if len(preview) > 120:
                preview = preview[:117] + '...'
            self.listbox.insert(
                tk.END,
                f"#{candidate['index'] + 1} [{candidate['source']}] {preview}",
            )
        self.listbox.selection_set(0)
        self.listbox.bind('<Double-Button-1>', lambda _event: self.confirm())

        button_row = ttk.Frame(container)
        button_row.pack(fill=tk.X)
        ttk.Button(button_row, text='下载选中项', command=self.confirm).pack(side=tk.LEFT)
        ttk.Button(button_row, text='取消任务', command=self.cancel).pack(side=tk.RIGHT)

        self.protocol('WM_DELETE_WINDOW', self.cancel)
        self.transient(master)
        self.grab_set()
        self.focus()

    def confirm(self) -> None:
        selection = self.listbox.curselection()
        index = selection[0] if selection else 0
        candidate = self._candidates[index]
        self.destroy()
        self._on_confirm(candidate['index'])

    def cancel(self) -> None:
        self.destroy()
        self._on_cancel()


class RenweiGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title('人卫电子书下载与修复')
        self.root.geometry('980x760')

        self.events: queue.Queue = queue.Queue()
        self.downloader_proc: subprocess.Popen[str] | None = None
        self.repair_proc: subprocess.Popen[str] | None = None
        self.candidate_dialog: CandidateDialog | None = None
        self.awaiting_continue = False
        self.downloader_has_terminal_event = False
        self.pending_auto_repair = False
        self.repair_failed = False
        self.current_manifest_path = ''
        self.current_processing_metadata_path = ''
        self.current_pdf_password = ''

        defaults = {
            'url': '',
            'output_dir': str(DOWNLOADER_DIR / 'output'),
            'browser_path': DEFAULT_BROWSER_PATH,
            'profile_dir': str(DOWNLOADER_DIR / '.playwright-profile'),
            'match': '',
            'font_dir': str(REPAIR_DIR / 'tmp_fonts_repo'),
            'overrides_path': '',
            'export_dir': '',
            'auto_repair': True,
        }
        defaults.update(self.load_settings())

        self.url_var = tk.StringVar(value=defaults['url'])
        self.output_dir_var = tk.StringVar(value=defaults['output_dir'])
        self.browser_path_var = tk.StringVar(value=defaults['browser_path'])
        self.profile_dir_var = tk.StringVar(value=defaults['profile_dir'])
        self.match_var = tk.StringVar(value=defaults['match'])
        self.pdf_password_var = tk.StringVar(value='')
        self.font_dir_var = tk.StringVar(value=defaults['font_dir'])
        self.overrides_var = tk.StringVar(value=defaults['overrides_path'])
        self.export_dir_var = tk.StringVar(value=defaults['export_dir'])
        self.source_pdf_var = tk.StringVar(value='')
        self.repaired_pdf_var = tk.StringVar(value='')
        self.status_var = tk.StringVar(value='就绪')
        self.auto_repair_var = tk.BooleanVar(value=bool(defaults['auto_repair']))
        self.advanced_visible = False

        self.build_ui()
        self.update_button_states()
        self.root.after(100, self.process_events)
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)

    def load_settings(self) -> dict:
        if not SETTINGS_PATH.exists():
            return {}
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
        except Exception:
            return {}

    def save_settings(self) -> None:
        payload = {
            'url': self.url_var.get().strip(),
            'output_dir': self.output_dir_var.get().strip(),
            'browser_path': self.browser_path_var.get().strip(),
            'profile_dir': self.profile_dir_var.get().strip(),
            'match': self.match_var.get().strip(),
            'font_dir': self.font_dir_var.get().strip(),
            'overrides_path': self.overrides_var.get().strip(),
            'export_dir': self.export_dir_var.get().strip(),
            'auto_repair': bool(self.auto_repair_var.get()),
        }
        SETTINGS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    def build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=12)
        container.pack(fill=tk.BOTH, expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        download_frame = ttk.LabelFrame(container, text='下载配置', padding=12)
        download_frame.grid(row=0, column=0, sticky='ew')
        download_frame.columnconfigure(1, weight=1)

        ttk.Label(download_frame, text='书页地址').grid(row=0, column=0, sticky='w')
        ttk.Entry(download_frame, textvariable=self.url_var).grid(row=0, column=1, sticky='ew', padx=(8, 0))

        toggle_row = ttk.Frame(download_frame)
        toggle_row.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(10, 0))
        ttk.Button(toggle_row, text='显示高级选项', command=self.toggle_advanced).pack(side=tk.LEFT)

        self.advanced_frame = ttk.Frame(download_frame)
        self.advanced_frame.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(10, 0))
        self.advanced_frame.columnconfigure(1, weight=1)
        self.add_path_row(self.advanced_frame, 0, '输出目录', self.output_dir_var, True)
        self.add_path_row(self.advanced_frame, 1, '浏览器路径', self.browser_path_var, False)
        self.add_path_row(self.advanced_frame, 2, '登录配置目录', self.profile_dir_var, True)
        ttk.Label(self.advanced_frame, text='候选过滤 match').grid(row=3, column=0, sticky='w', pady=(8, 0))
        ttk.Entry(self.advanced_frame, textvariable=self.match_var).grid(row=3, column=1, sticky='ew', padx=(8, 8), pady=(8, 0))
        ttk.Label(self.advanced_frame, text='手动 PDF 密码').grid(row=4, column=0, sticky='w', pady=(8, 0))
        ttk.Entry(self.advanced_frame, textvariable=self.pdf_password_var, show='*').grid(row=4, column=1, sticky='ew', padx=(8, 8), pady=(8, 0))
        self.advanced_frame.grid_remove()

        status_frame = ttk.LabelFrame(container, text='运行状态与操作', padding=12)
        status_frame.grid(row=1, column=0, sticky='nsew', pady=(12, 0))
        status_frame.columnconfigure(0, weight=1)
        status_frame.rowconfigure(2, weight=1)

        ttk.Label(status_frame, textvariable=self.status_var, wraplength=900).grid(row=0, column=0, sticky='ew')

        button_row = ttk.Frame(status_frame)
        button_row.grid(row=1, column=0, sticky='ew', pady=(10, 10))
        self.start_button = ttk.Button(button_row, text='启动浏览器并开始下载', command=self.start_download)
        self.start_button.pack(side=tk.LEFT)
        self.continue_button = ttk.Button(button_row, text='我已打开 PDF 页面，继续检测', command=self.continue_detection)
        self.continue_button.pack(side=tk.LEFT, padx=(8, 0))
        self.repair_button = ttk.Button(button_row, text='开始修复当前 PDF', command=lambda: self.start_repair(auto_trigger=False))
        self.repair_button.pack(side=tk.LEFT, padx=(8, 0))
        self.cancel_button = ttk.Button(button_row, text='取消当前任务', command=self.cancel_current_task)
        self.cancel_button.pack(side=tk.RIGHT)

        self.log_text = tk.Text(status_frame, height=18, wrap='word', state='disabled')
        self.log_text.grid(row=2, column=0, sticky='nsew')
        log_scroll = ttk.Scrollbar(status_frame, orient='vertical', command=self.log_text.yview)
        log_scroll.grid(row=2, column=1, sticky='ns')
        self.log_text.configure(yscrollcommand=log_scroll.set)

        repair_frame = ttk.LabelFrame(container, text='自动修复与结果', padding=12)
        repair_frame.grid(row=2, column=0, sticky='ew', pady=(12, 0))
        repair_frame.columnconfigure(1, weight=1)

        self.add_path_row(repair_frame, 0, '待修复 PDF', self.source_pdf_var, False)
        ttk.Checkbutton(repair_frame, text='检测到乱码时自动修复', variable=self.auto_repair_var).grid(
            row=1, column=0, columnspan=3, sticky='w', pady=(8, 0)
        )
        self.add_path_row(repair_frame, 2, '字体目录', self.font_dir_var, True)
        self.add_path_row(repair_frame, 3, 'overrides JSON', self.overrides_var, False)
        ttk.Label(repair_frame, text='修复输出').grid(row=4, column=0, sticky='w', pady=(8, 0))
        ttk.Entry(repair_frame, textvariable=self.repaired_pdf_var, state='readonly').grid(
            row=4, column=1, sticky='ew', padx=(8, 8), pady=(8, 0)
        )
        self.add_path_row(repair_frame, 5, '导出目录', self.export_dir_var, True)

        open_row = ttk.Frame(repair_frame)
        open_row.grid(row=6, column=0, columnspan=3, sticky='ew', pady=(10, 0))
        ttk.Button(open_row, text='打开源 PDF', command=lambda: open_path(self.source_pdf_var.get().strip())).pack(side=tk.LEFT)
        ttk.Button(open_row, text='打开修复后 PDF', command=lambda: open_path(self.repaired_pdf_var.get().strip())).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(open_row, text='打开所在目录', command=self.open_result_folder).pack(side=tk.LEFT, padx=(8, 0))
        self.export_cleanup_button = ttk.Button(open_row, text='导出并清理', command=self.export_and_cleanup)
        self.export_cleanup_button.pack(side=tk.RIGHT)

    def add_path_row(self, frame: ttk.Frame, row: int, label: str, variable: tk.StringVar, directory: bool) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', pady=(8 if row else 0, 0))
        ttk.Entry(frame, textvariable=variable).grid(row=row, column=1, sticky='ew', padx=(8, 8), pady=(8 if row else 0, 0))
        command = (lambda: self.choose_directory(variable)) if directory else (lambda: self.choose_file(variable))
        ttk.Button(frame, text='浏览', command=command).grid(row=row, column=2, pady=(8 if row else 0, 0))

    def toggle_advanced(self) -> None:
        self.advanced_visible = not self.advanced_visible
        if self.advanced_visible:
            self.advanced_frame.grid()
        else:
            self.advanced_frame.grid_remove()

    def choose_directory(self, variable: tk.StringVar) -> None:
        selected = filedialog.askdirectory(initialdir=variable.get() or str(ROOT_DIR))
        if selected:
            variable.set(selected)

    def choose_file(self, variable: tk.StringVar) -> None:
        selected = filedialog.askopenfilename(initialdir=str(Path(variable.get()).parent if variable.get() else ROOT_DIR))
        if selected:
            variable.set(selected)

    def append_log(self, message: str) -> None:
        self.log_text.configure(state='normal')
        self.log_text.insert(tk.END, message.rstrip() + '\n')
        self.log_text.see(tk.END)
        self.log_text.configure(state='disabled')

    def update_button_states(self) -> None:
        downloader_running = self.downloader_proc is not None and self.downloader_proc.poll() is None
        repair_running = self.repair_proc is not None and self.repair_proc.poll() is None
        source_ready = bool(self.source_pdf_var.get().strip())
        final_ready = bool(self.get_existing_final_pdf_path())

        self.start_button.configure(state='disabled' if downloader_running or repair_running else 'normal')
        self.continue_button.configure(state='normal' if downloader_running and self.awaiting_continue else 'disabled')
        self.cancel_button.configure(state='normal' if downloader_running or repair_running else 'disabled')
        self.repair_button.configure(state='normal' if source_ready and not downloader_running and not repair_running else 'disabled')
        self.export_cleanup_button.configure(state='normal' if final_ready and not downloader_running and not repair_running else 'disabled')

    def start_download(self) -> None:
        if not DOWNLOADER_ENTRY.exists():
            messagebox.showerror('缺少入口', f'找不到下载器入口:\n{DOWNLOADER_ENTRY}')
            return
        if self.downloader_proc or self.repair_proc:
            return

        self.save_settings()
        self.awaiting_continue = False
        self.downloader_has_terminal_event = False
        self.pending_auto_repair = False
        self.current_manifest_path = ''
        self.current_processing_metadata_path = ''
        self.current_pdf_password = ''
        self.repaired_pdf_var.set('')
        self.source_pdf_var.set('')
        self.status_var.set('正在启动浏览器...')
        self.append_log('正在启动下载流程。')

        command = ['node', str(DOWNLOADER_ENTRY), '--gui-bridge']
        url = self.url_var.get().strip()
        if url:
            command.extend(['--url', url])
        self.append_optional_arg(command, '--output-dir', self.output_dir_var.get())
        self.append_optional_arg(command, '--browser-path', self.browser_path_var.get())
        self.append_optional_arg(command, '--profile-dir', self.profile_dir_var.get())
        self.append_optional_arg(command, '--match', self.match_var.get())
        self.append_optional_arg(command, '--pdf-password', self.pdf_password_var.get())

        self.downloader_proc = subprocess.Popen(
            command,
            cwd=str(DOWNLOADER_DIR),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
        )
        start_pipe_reader(self.downloader_proc.stdout, self.events, 'downloader_event', parse_json=True)
        start_pipe_reader(self.downloader_proc.stderr, self.events, 'downloader_stderr')
        threading.Thread(target=self.wait_for_downloader_exit, daemon=True).start()
        self.update_button_states()

    def append_optional_arg(self, command: list[str], name: str, value: str) -> None:
        if value.strip():
            command.extend([name, value.strip()])

    def wait_for_downloader_exit(self) -> None:
        if self.downloader_proc is None:
            return
        code = self.downloader_proc.wait()
        self.events.put(('downloader_exit', code))

    def wait_for_repair_exit(self) -> None:
        if self.repair_proc is None:
            return
        code = self.repair_proc.wait()
        self.events.put(('repair_exit', code))

    def continue_detection(self) -> None:
        self.awaiting_continue = False
        self.send_downloader_command({'type': 'continue'})
        self.status_var.set('已发送继续检测指令，正在抓取候选 PDF...')
        self.update_button_states()

    def send_downloader_command(self, payload: dict) -> None:
        proc = self.downloader_proc
        if not proc or proc.poll() is not None or proc.stdin is None:
            return
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + '\n')
        proc.stdin.flush()

    def rename_final_pdf(self, pdf_path_str: str, password: str = '') -> str:
        pdf_path = Path(pdf_path_str)
        if not pdf_path.exists():
            return pdf_path_str

        try:
            book_title = extract_book_title(pdf_path, password)
        except Exception as exc:
            self.append_log(f'读取第{TITLE_PAGE_NUMBER}页标题失败，保留原文件名: {exc}')
            return pdf_path_str

        safe_stem = sanitize_filename_stem(book_title)
        if not safe_stem:
            self.append_log(f'未能从第{TITLE_PAGE_NUMBER}页提取到有效书名，保留原文件名。')
            return pdf_path_str

        target_path = make_unique_path(pdf_path.with_name(f'{safe_stem}{pdf_path.suffix}'))
        if target_path == pdf_path:
            self.append_log(f'最终文件名已是目标名称: {pdf_path.name}')
            return pdf_path_str

        try:
            renamed_path = pdf_path.rename(target_path)
        except Exception as exc:
            self.append_log(f'重命名最终文件失败，保留原文件名: {exc}')
            return pdf_path_str

        self.append_log(f'最终文件已重命名为: {renamed_path.name}')
        return str(renamed_path)

    def get_existing_final_pdf_path(self) -> str:
        repaired_pdf = self.repaired_pdf_var.get().strip()
        if repaired_pdf and Path(repaired_pdf).exists():
            return repaired_pdf
        source_pdf = self.source_pdf_var.get().strip()
        if source_pdf and Path(source_pdf).exists():
            return source_pdf
        return ''

    def get_run_directory(self) -> Path | None:
        tracked_paths = [
            self.current_manifest_path,
            self.current_processing_metadata_path,
            self.repaired_pdf_var.get().strip(),
            self.source_pdf_var.get().strip(),
        ]
        for path_str in tracked_paths:
            if path_str:
                return Path(path_str).resolve().parent
        return None

    def choose_export_directory_for_result(self) -> Path | None:
        export_dir = self.export_dir_var.get().strip()
        if export_dir:
            return Path(export_dir)

        selected = filedialog.askdirectory(initialdir=str(ROOT_DIR))
        if not selected:
            return None
        self.export_dir_var.set(selected)
        self.save_settings()
        return Path(selected)

    def cleanup_generated_files(self, run_dir: Path, exported_pdf: Path) -> tuple[list[str], list[str]]:
        removed: list[str] = []
        failed: list[str] = []
        output_root = Path(self.output_dir_var.get().strip()).resolve() if self.output_dir_var.get().strip() else None
        safe_to_remove_dir = (
            bool(self.current_manifest_path)
            or bool(self.current_processing_metadata_path)
            or (run_dir / 'segments').exists()
            or (output_root is not None and run_dir != output_root and path_is_within(run_dir, output_root))
        )

        if safe_to_remove_dir and exported_pdf.parent.resolve() != run_dir.resolve():
            try:
                shutil.rmtree(run_dir)
                removed.append(str(run_dir))
                return removed, failed
            except Exception as exc:
                failed.append(f'{run_dir}: {exc}')

        file_targets: set[Path] = set()
        dir_targets: set[Path] = set()
        tracked_files = [
            self.source_pdf_var.get().strip(),
            self.repaired_pdf_var.get().strip(),
            self.current_manifest_path,
            self.current_processing_metadata_path,
        ]
        for path_str in tracked_files:
            if path_str:
                path = Path(path_str)
                if path.exists() and path.resolve() != exported_pdf.resolve():
                    file_targets.add(path)

        for pattern in ('*.font_mapping.json', '*.page_report.json'):
            for sidecar in run_dir.glob(pattern):
                if sidecar.resolve() != exported_pdf.resolve():
                    file_targets.add(sidecar)

        segments_dir = run_dir / 'segments'
        if segments_dir.exists():
            dir_targets.add(segments_dir)

        for target in sorted(file_targets, key=lambda item: str(item)):
            try:
                target.unlink()
                removed.append(str(target))
            except Exception as exc:
                failed.append(f'{target}: {exc}')

        for target in sorted(dir_targets, key=lambda item: str(item)):
            try:
                shutil.rmtree(target)
                removed.append(str(target))
            except Exception as exc:
                failed.append(f'{target}: {exc}')

        try:
            if run_dir.exists() and not any(run_dir.iterdir()):
                run_dir.rmdir()
                removed.append(str(run_dir))
        except Exception as exc:
            failed.append(f'{run_dir}: {exc}')

        return removed, failed

    def export_and_cleanup(self) -> None:
        final_pdf_str = self.get_existing_final_pdf_path()
        if not final_pdf_str:
            messagebox.showerror('缺少结果文件', '当前没有可导出的最终 PDF。')
            return

        final_pdf = Path(final_pdf_str)
        if not final_pdf.exists():
            messagebox.showerror('找不到结果文件', f'当前结果文件不存在:\n{final_pdf}')
            return

        export_dir = self.choose_export_directory_for_result()
        if export_dir is None:
            return

        try:
            export_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror('导出目录不可用', f'无法创建导出目录:\n{export_dir}\n\n{exc}')
            return

        run_dir = self.get_run_directory()
        try:
            export_dir_resolved = export_dir.resolve()
        except Exception:
            export_dir_resolved = export_dir
        if run_dir is not None and (export_dir_resolved == run_dir.resolve() or path_is_within(export_dir_resolved, run_dir.resolve())):
            messagebox.showerror('导出目录无效', '导出目录不能和当前运行目录相同，也不能位于当前运行目录内。')
            return

        target_path = make_unique_path(export_dir / final_pdf.name)
        try:
            shutil.copy2(final_pdf, target_path)
        except Exception as exc:
            messagebox.showerror('导出失败', f'导出 PDF 失败:\n{target_path}\n\n{exc}')
            return

        removed: list[str] = []
        failed: list[str] = []
        if run_dir is not None and run_dir.exists():
            removed, failed = self.cleanup_generated_files(run_dir, target_path)

        self.source_pdf_var.set('')
        self.repaired_pdf_var.set(str(target_path))
        self.current_manifest_path = ''
        self.current_processing_metadata_path = ''
        self.current_pdf_password = ''
        self.status_var.set(f'已导出到 {target_path}，并完成清理。')
        self.append_log(f'最终 PDF 已导出到: {target_path}')
        if removed:
            self.append_log(f'已清理 {len(removed)} 项运行产物。')
        if failed:
            self.append_log('以下文件未能自动清理：')
            for item in failed:
                self.append_log(f'  - {item}')
        self.update_button_states()

    def inspect_text_layer_and_maybe_repair(self) -> None:
        source_pdf = self.source_pdf_var.get().strip()
        if not source_pdf:
            self.status_var.set('下载完成，但未找到待检查的 PDF。')
            return

        source_path = Path(source_pdf)
        if not source_path.exists():
            self.status_var.set('下载完成，但目标 PDF 已不存在。')
            self.append_log(f'找不到待检查的 PDF: {source_path}')
            return

        self.status_var.set('正在检查第1、6、20页文字层...')
        self.append_log('开始抽样检查第1、6、20页文字层。')

        try:
            checked_pages, garbled_pages = collect_page_samples(source_path, self.current_pdf_password)
        except Exception as exc:
            self.status_var.set('文字层抽样检查失败。')
            self.append_log(f'文字层抽样检查失败: {exc}')
            return

        if checked_pages:
            checked = '、'.join(str(page) for page in checked_pages)
            self.append_log(f'已检查页码: {checked}')
        else:
            self.append_log('目标 PDF 页数不足，未命中抽样页，直接进入重命名。')

        if garbled_pages:
            self.append_log(f'检测到疑似乱码页: {"；".join(garbled_pages)}')
            if self.auto_repair_var.get():
                self.status_var.set('检测到疑似乱码，正在自动修复...')
                self.start_repair(auto_trigger=True)
            else:
                self.status_var.set('检测到疑似乱码，但已关闭自动修复。')
                self.append_log('已关闭自动修复，本次保留原 PDF。')
                renamed_path = self.rename_final_pdf(source_pdf, self.current_pdf_password)
                self.source_pdf_var.set(renamed_path)
                self.repaired_pdf_var.set(renamed_path)
            return

        renamed_path = self.rename_final_pdf(source_pdf, self.current_pdf_password)
        self.source_pdf_var.set(renamed_path)
        self.repaired_pdf_var.set(renamed_path)
        self.status_var.set('文字层抽样通过，最终文件已整理完成。')

    def start_repair(self, auto_trigger: bool) -> None:
        if self.downloader_proc or self.repair_proc:
            return
        source_pdf = self.source_pdf_var.get().strip()
        font_dir = self.font_dir_var.get().strip()
        if not source_pdf:
            if not auto_trigger:
                messagebox.showerror('缺少 PDF', '请先下载 PDF，或手动选择待修复 PDF。')
            return
        if not Path(source_pdf).exists():
            messagebox.showerror('找不到 PDF', f'待修复文件不存在:\n{source_pdf}')
            return
        if not font_dir or not Path(font_dir).exists():
            messagebox.showerror('字体目录无效', f'字体目录不存在:\n{font_dir}')
            return

        self.save_settings()
        output_pdf = str(Path(source_pdf).with_name(Path(source_pdf).stem + '-repaired.pdf'))
        self.repaired_pdf_var.set(output_pdf)
        self.repair_failed = False
        self.status_var.set('正在修复 PDF...')
        self.append_log(f'开始修复: {source_pdf}')

        command = [sys.executable, str(REPAIR_ENTRY), source_pdf, font_dir, output_pdf]
        overrides = self.overrides_var.get().strip()
        if overrides:
            command.extend(['--overrides', overrides])

        self.repair_proc = subprocess.Popen(
            command,
            cwd=str(REPAIR_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
        )
        start_pipe_reader(self.repair_proc.stdout, self.events, 'repair_stdout')
        start_pipe_reader(self.repair_proc.stderr, self.events, 'repair_stderr')
        threading.Thread(target=self.wait_for_repair_exit, daemon=True).start()
        self.update_button_states()

    def cancel_current_task(self) -> None:
        if self.downloader_proc and self.downloader_proc.poll() is None:
            self.status_var.set('正在取消下载...')
            self.append_log('请求取消下载任务。')
            self.send_downloader_command({'type': 'cancel'})
            proc = self.downloader_proc
            self.root.after(1200, lambda: self.force_terminate(proc))
        elif self.repair_proc and self.repair_proc.poll() is None:
            self.status_var.set('正在取消修复...')
            self.append_log('正在停止修复任务。')
            self.force_terminate(self.repair_proc)

    def force_terminate(self, proc: subprocess.Popen[str] | None) -> None:
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def handle_downloader_event(self, payload: dict) -> None:
        event_type = payload.get('type')
        if event_type == 'status':
            message = payload.get('message', '')
            if message:
                self.status_var.set(message)
                self.append_log(message)
        elif event_type == 'await_continue':
            self.awaiting_continue = True
            self.status_var.set(payload.get('message', '请继续检测'))
            self.append_log(payload.get('message', '请继续检测'))
        elif event_type == 'choose_candidate':
            self.awaiting_continue = False
            candidates = payload.get('candidates', [])
            self.status_var.set('请选择一个 PDF 候选。')
            self.append_log('检测到多个 PDF 候选，等待选择。')
            self.candidate_dialog = CandidateDialog(self.root, candidates, self.on_candidate_selected, self.on_candidate_cancel)
        elif event_type == 'download_complete':
            self.downloader_has_terminal_event = True
            source_pdf = payload.get('sourcePdfPath', '')
            self.source_pdf_var.set(source_pdf)
            self.current_manifest_path = payload.get('manifestPath') or ''
            self.current_processing_metadata_path = payload.get('processingMetadataPath') or ''
            self.current_pdf_password = payload.get('passwordUsed') or ''
            self.status_var.set('下载完成。')
            self.append_log(f'下载完成: {source_pdf}')
            self.pending_auto_repair = True
            self.status_var.set('下载完成，等待下载器收尾后检查文字层...')
        elif event_type == 'error':
            self.downloader_has_terminal_event = True
            self.pending_auto_repair = False
            message = payload.get('message', '下载失败')
            self.status_var.set(message)
            self.append_log(f'下载失败: {message}')
        elif event_type == 'cancelled':
            self.downloader_has_terminal_event = True
            self.pending_auto_repair = False
            message = payload.get('message', '下载已取消')
            self.status_var.set(message)
            self.append_log(message)
        self.update_button_states()

    def on_candidate_selected(self, index: int) -> None:
        self.candidate_dialog = None
        self.send_downloader_command({'type': 'select_candidate', 'index': index})
        self.status_var.set('已发送候选选择，开始下载...')
        self.append_log(f'已选择候选 #{index + 1}。')

    def on_candidate_cancel(self) -> None:
        self.candidate_dialog = None
        self.cancel_current_task()

    def open_result_folder(self) -> None:
        final_pdf = self.get_existing_final_pdf_path()
        if final_pdf:
            open_path(str(Path(final_pdf).parent))

    def process_events(self) -> None:
        while True:
            try:
                event_name, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if event_name == 'downloader_event':
                self.handle_downloader_event(payload)
            elif event_name == 'downloader_stderr':
                self.append_log(f'[downloader] {payload}')
            elif event_name == 'downloader_exit':
                self.awaiting_continue = False
                self.downloader_proc = None
                if self.candidate_dialog is not None:
                    self.candidate_dialog.destroy()
                    self.candidate_dialog = None
                if payload == 0 and not self.downloader_has_terminal_event:
                    self.status_var.set('下载流程结束。')
                elif payload != 0 and not self.downloader_has_terminal_event:
                    self.status_var.set(f'下载器异常退出，退出码 {payload}')
                    self.append_log(f'下载器异常退出，退出码 {payload}')
                if payload != 0:
                    self.pending_auto_repair = False
                if payload == 0 and self.pending_auto_repair and self.source_pdf_var.get().strip():
                    self.pending_auto_repair = False
                    self.inspect_text_layer_and_maybe_repair()
                self.update_button_states()
            elif event_name == 'repair_stdout':
                self.append_log(f'[repair] {payload}')
            elif event_name == 'repair_stderr':
                self.append_log(f'[repair] {payload}')
            elif event_name == 'repair_exit':
                self.repair_proc = None
                if payload == 0 and not self.repair_failed:
                    final_pdf = self.rename_final_pdf(self.repaired_pdf_var.get().strip())
                    self.repaired_pdf_var.set(final_pdf)
                    self.status_var.set('PDF 修复完成，最终文件已整理完成。')
                    self.append_log(f'修复完成: {final_pdf}')
                else:
                    self.status_var.set(f'PDF 修复失败，退出码 {payload}')
                    self.append_log(f'PDF 修复失败，退出码 {payload}')
                self.update_button_states()
            elif event_name == 'log':
                self.append_log(payload)

        self.root.after(100, self.process_events)

    def on_close(self) -> None:
        self.save_settings()
        self.force_terminate(self.downloader_proc)
        self.force_terminate(self.repair_proc)
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    style = ttk.Style(root)
    if 'vista' in style.theme_names():
        style.theme_use('vista')
    app = RenweiGui(root)
    root.mainloop()


if __name__ == '__main__':
    main()

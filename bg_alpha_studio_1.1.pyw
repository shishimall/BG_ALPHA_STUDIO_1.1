# -*- coding: utf-8 -*-
"""背景一発除去・透過MOV化: 動画 -> PNG連番 -> Photoshop背景除去 -> 透過MOV.

Integrates the workflows of the supplied video_to_frames.pyw, Photoshop JSX,
and sequence_connector_dnd.pyw, while keeping Photoshop's original
`removeBackground` Action Manager operation and QTRLE/ARGB alpha MOV format.
Windows target.  Requires CustomTkinter; FFmpeg and Adobe Photoshop are required.
"""
import os
import re
import math
import shutil
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

try:
    import customtkinter as ctk
except ImportError:
    # .pyw runs without a console, so a missing package needs a visible explanation.
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        '背景一発除去・透過MOV化 - 起動できません',
        'CustomTkinterが見つかりません。\n\n'
        '開発用Python環境で次を実行してください。\n'
        'python -m pip install customtkinter\n\n'
        'EXE版を利用する場合、配布先でのインストールは不要です。'
    )
    root.destroy()
    raise SystemExit(1)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        '背景一発除去・透過MOV化 - 起動できません',
        'ドラッグ＆ドロップ用の tkinterdnd2 が見つかりません。\n\n'
        '次のコマンドで必要な部品をインストールしてください。\n'
        'python -m pip install -r requirements.txt\n\n'
        'または python -m pip install tkinterdnd2\n\n'
        'EXE版では追加インストール不要です。'
    )
    root.destroy()
    raise SystemExit(1)

# フォルダー名は英大文字、ファイル名は英小文字、画面タイトルは日本語。
# 版数だけを実際の .pyw / .exe のファイル名末尾から取得する。
APP_CODE = 'bg_alpha_studio'
APP_BASE_TITLE = '背景一発除去・透過MOV化'


def app_version(file_path=None):
    """リネーム追従: _1.02.pyw / _1.09.exe / _1.09(1).pyw に対応。"""
    if file_path is None:
        file_path = sys.executable if getattr(sys, 'frozen', False) else __file__
    stem = Path(file_path).stem
    match = re.search(r'_(\d+(?:\.\d+)+)(?:\(\d+\))?$', stem)
    return match.group(1) if match else '未設定'


VERSION = app_version()
APP_TITLE = f'{APP_BASE_TITLE} Ver.{VERSION}'


CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
FRAME_PATTERN = 'frame_%06d.png'
VIDEO_FILTERS = [
    ('動画ファイル', '*.mp4 *.mov *.mkv *.avi *.webm *.m4v'),
    ('すべてのファイル', '*.*'),
]
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v'}


def base_dir():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def ffmpeg_candidates():
    # In onefile EXE, PyInstaller extracts the bundled FFmpeg to _MEIPASS.
    folder = base_dir()
    yield folder / 'ffmpeg.exe'
    yield folder / 'ffmpeg'
    for bundled in folder.glob('ffmpeg*.exe'):
        yield bundled
    yield Path(r'C:\ffmpeg-2024-11-21-git-f298507323-full_build\bin\ffmpeg.exe')
    yield Path(r'C:\Python314\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe')
    located = shutil.which('ffmpeg')
    if located:
        yield Path(located)
    try:
        import imageio_ffmpeg
        yield Path(imageio_ffmpeg.get_ffmpeg_exe())
    except ImportError:
        pass


def find_ffmpeg():
    visited = set()
    for candidate in ffmpeg_candidates():
        candidate = str(candidate)
        if candidate.lower() in visited:
            continue
        visited.add(candidate.lower())
        if not Path(candidate).is_file():
            continue
        try:
            p = subprocess.run([candidate, '-version'], capture_output=True,
                               creationflags=CREATE_NO_WINDOW, timeout=8)
            if p.returncode == 0:
                return candidate
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def display_name(filename):
    result = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', filename).strip(' .')
    return (result[:65] or 'video')


def new_project_folder(destination, name):
    base = destination / (display_name(name) + '_透過変換')
    folder = base
    i = 2
    while folder.exists():
        folder = Path(str(base) + '_' + str(i))
        i += 1
    folder.mkdir(parents=True)
    return folder


def command(args, description):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            encoding='utf-8', errors='replace', creationflags=CREATE_NO_WINDOW)
    if result.returncode != 0:
        log = (result.stderr or result.stdout or '詳細なし')[-2500:]
        raise RuntimeError(f'{description}に失敗しました。\n{log}')
    return result


def source_fps(ffmpeg, movie):
    result = subprocess.run([ffmpeg, '-hide_banner', '-i', str(movie)],
                            capture_output=True, encoding='utf-8', errors='replace',
                            creationflags=CREATE_NO_WINDOW)
    # FFmpeg deliberately exits nonzero if no output file is specified.
    for line in result.stderr.splitlines():
        if 'Video:' in line and 'Stream #' in line:
            match = re.search(r'(?<![\w.])(\d+(?:\.\d+)?)\s+fps\b', line)
            if match:
                return match.group(1)
    return None


def frame_count(folder):
    return len(list(folder.glob('frame_*.png')))


def video_geometry(ffmpeg, movie):
    """Inspect the *input*, without modifying the input or video pipeline.

    ffmpeg -i is used instead of ffprobe so the bundled EXE needs only FFmpeg.
    Its nonzero exit status (without output) is intentional.
    """
    result = subprocess.run(
        [ffmpeg, '-hide_banner', '-i', str(movie)],
        capture_output=True, encoding='utf-8', errors='replace',
        creationflags=CREATE_NO_WINDOW, timeout=20,
    )
    lines = result.stderr.splitlines()
    for i, line in enumerate(lines):
        if not re.search(r'Stream\s+#\d+:\d+', line) or 'Video:' not in line:
            continue
        video_part = line.split('Video:', 1)[1]
        dimensions = re.search(r'(?<![\dx])(\d{2,5})\s*x\s*(\d{2,5})(?!\d)', video_part)
        if not dimensions:
            continue
        width, height = map(int, dimensions.groups())
        if width < 16 or height < 16:
            continue
        gcd = math.gcd(width, height)
        ratio = f'{width // gcd}:{height // gcd}'
        # Display aspect (DAR) may differ from the pixel frame ratio.
        dar = re.search(r'\bDAR\s+(\d+):(\d+)', video_part)
        display_ratio = None
        if dar and int(dar.group(2)):
            num, den = int(dar.group(1)), int(dar.group(2))
            dgcd = math.gcd(num, den)
            display_ratio = f'{num // dgcd}:{den // dgcd}'
        rotation = None
        # Rotation metadata is nested below the corresponding stream entry.
        for next_line in lines[i + 1:]:
            if re.search(r'^\s*Stream\s+#\d+:\d+', next_line):
                break
            rotated = re.search(r'rotation of\s+(-?\d+(?:\.\d+)?)\s+degrees', next_line)
            if rotated:
                rotation = float(rotated.group(1))
                break
        return width, height, ratio, display_ratio, rotation
    raise RuntimeError('FFmpegから動画の幅・高さを読み取れませんでした。')


def geometry_message(info):
    width, height, ratio, display_ratio, rotation = info
    orientation = '横長' if width > height else '縦長' if height > width else '正方形'
    msg = f'入力アスペクト：{ratio}（{width}×{height} px / {orientation}）'
    if display_ratio and display_ratio != ratio:
        msg += f' / 表示アスペクト(DAR)：{display_ratio}'
    if rotation is not None and abs(rotation) > 0.01:
        msg += f' / 回転メタデータ：{rotation:g}°'
    return msg


# This Photoshop script retains the exact background removal Action Manager call,
# layer normalization and PNG save flow from the provided JSX.  Folder picking and
# popups are replaced by generated paths and a result log for automation.
JSX_TEMPLATE = r'''#target photoshop
app.displayDialogs = DialogModes.NO;
var inputFolder = new Folder(__INPUT__);
var outputFolder = new Folder(__OUTPUT__);
var logFile = new File(__LOG__);
var progressFile = new File(__PROGRESS__);
if (!inputFolder.exists) throw new Error('Input frames folder not found: ' + inputFolder.fsName);
if (!outputFolder.exists && !outputFolder.create()) throw new Error('Could not make output folder');
var files = inputFolder.getFiles(function(x) {
    return x instanceof File && /^frame_\d+\.png$/i.test(x.name);
});
files.sort(function(a,b) { return a.name < b.name ? -1 : (a.name > b.name ? 1 : 0); });
var successCount = 0, skipCount = 0, errors = [];
var processedCount = 0;
function writeProgress(current) {
    // The UI reads this small text file while Photoshop is busy.
    // This does not affect any output images or the removal algorithm.
    progressFile.encoding = 'UTF8';
    if (progressFile.open('w')) {
        progressFile.writeln('current=' + current);
        progressFile.writeln('processed=' + processedCount);
        progressFile.writeln('total=' + files.length);
        progressFile.writeln('success=' + successCount);
        progressFile.writeln('skip=' + skipCount);
        progressFile.writeln('errors=' + errors.length);
        progressFile.close();
    }
}
writeProgress(0);
function removeBackground() {
    var idremoveBackground = stringIDToTypeID('removeBackground');
    executeAction(idremoveBackground, undefined, DialogModes.NO);
}
for (var i = 0; i < files.length; i++) {
    writeProgress(i + 1);
    var file = files[i];
    var saveFile = new File(outputFolder.fsName + '/' + file.name);
    var doc = null;
    try {
        if (saveFile.exists) { skipCount++; continue; }
        doc = open(file);
        if (doc.mode != DocumentMode.RGB) doc.changeMode(ChangeMode.RGB);
        if (doc.bitsPerChannel != BitsPerChannelType.EIGHT)
            doc.bitsPerChannel = BitsPerChannelType.EIGHT;
        if (doc.activeLayer.isBackgroundLayer)
            doc.activeLayer.isBackgroundLayer = false;
        removeBackground();
        var opts = new PNGSaveOptions();
        doc.saveAs(saveFile, opts, true);
        doc.close(SaveOptions.DONOTSAVECHANGES);
        doc = null;
        successCount++;
    } catch (e) {
        errors.push(file.name + ': ' + e.toString());
        try { if (doc) doc.close(SaveOptions.DONOTSAVECHANGES); } catch (ignored) {}
    } finally {
        processedCount++;
        writeProgress(i + 1);
    }
}
logFile.encoding = 'UTF8';
if (logFile.open('w')) {
    logFile.writeln('success=' + successCount);
    logFile.writeln('skip=' + skipCount);
    logFile.writeln('errors=' + errors.length);
    for (var k = 0; k < errors.length; k++) logFile.writeln(errors[k]);
    logFile.close();
}
'Success: ' + successCount + '; skip: ' + skipCount + '; errors: ' + errors.length;
'''


def js_string(path):
    # ASCII-only JS string literal, handles Japanese and Windows paths reliably.
    import json
    return json.dumps(str(path.resolve()).replace('\\', '/'), ensure_ascii=True)


def write_jsx(project, frames, alpha_frames):
    job = project / 'photoshop_remove_bg.jsx'
    log = project / 'photoshop_result.txt'
    progress = project / 'photoshop_progress.txt'
    jsx = (JSX_TEMPLATE.replace('__INPUT__', js_string(frames))
           .replace('__OUTPUT__', js_string(alpha_frames))
           .replace('__LOG__', js_string(log))
           .replace('__PROGRESS__', js_string(progress)))
    job.write_text(jsx, encoding='utf-8')
    return job, log, progress


def read_photoshop_progress(path):
    """Read the last completed heartbeat; malformed/partial reads are ignored."""
    try:
        contents = Path(path).read_text(encoding='utf-8-sig', errors='replace')
    except (FileNotFoundError, OSError):
        return None
    fields = dict(re.findall(r'^(current|processed|total|success|skip|errors)=(\d+)\s*$', contents, re.M))
    if not all(k in fields for k in ('current', 'processed', 'total')):
        return None
    values = {k: int(v) for k, v in fields.items()}
    if values['total'] < 1 or not (0 <= values['processed'] <= values['total']):
        return None
    if not (0 <= values['current'] <= values['total']):
        return None
    return values


def run_photoshop(jsx_path, project):
    if os.name != 'nt':
        raise RuntimeError('Photoshopの自動起動はWindows専用です。')
    bridge = project / 'photoshop_bridge.ps1'
    bridge.write_text(r'''param([Parameter(Mandatory=$true)][string]$ScriptPath)
$ErrorActionPreference = 'Stop'
try {
    $ps = New-Object -ComObject Photoshop.Application
    [void]$ps.DoJavaScriptFile($ScriptPath, @())
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
''', encoding='ascii')
    powershell = shutil.which('powershell.exe') or 'powershell.exe'
    command([powershell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
             '-File', str(bridge), '-ScriptPath', str(jsx_path)], 'Photoshopの自動処理')


def encode_mov(ffmpeg, alpha_dir, source_video, output, fps, count, audio):
    args = [ffmpeg, '-hide_banner', '-y', '-framerate', str(fps),
            '-start_number', '1', '-i', str(alpha_dir / FRAME_PATTERN)]
    if audio:
        args.extend(['-i', str(source_video)])
    args.extend(['-map', '0:v:0'])
    if audio:
        args.extend(['-map', '1:a:0?'])
    args.extend(['-c:v', 'qtrle', '-pix_fmt', 'argb'])
    if audio:
        args.extend(['-c:a', 'pcm_s16le', '-t', '{:.6f}'.format(count / float(fps))])
    args.append(str(output))
    command(args, '透過MOVの書き出し')


def transparency_present(ffmpeg, png):
    """Check that a Photoshop output contains actual nonopaque pixels."""
    cmd = [ffmpeg, '-v', 'error', '-i', str(png),
           '-vf', 'format=rgba,alphaextract,scale=64:64',
           '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'gray', 'pipe:1']
    result = subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW)
    return result.returncode == 0 and bool(result.stdout) and min(result.stdout) < 250




# -----------------------------------------------------------------------------
# CustomTkinter interface (3-stage pipeline; Photoshop integration is unchanged)
# -----------------------------------------------------------------------------
COLORS = {
    'background': '#0A1120',
    'panel': '#111D30',
    'panel_deep': '#0C1627',
    'outline': '#28384E',
    'text': '#F1F6FF',
    'secondary': '#91A7C4',
    'accent': '#31D6C1',
    'accent_hover': '#1FBAA7',
    'accent_text': '#062A2A',
    'blue': '#6C9BFF',
    'warning': '#F0BE75',
    'error': '#FF8585',
}


class BGAlphaStudio(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        ctk.set_appearance_mode('dark')
        ctk.set_default_color_theme('blue')
        super().__init__()
        self.title(APP_TITLE)
        self.geometry('960x770')
        self.minsize(790, 630)
        self.configure(fg_color=COLORS['background'])

        self.video = tk.StringVar()
        self.destination = tk.StringVar(value=str(Path.home() / 'Downloads'))
        self.fps_mode = tk.StringVar(value='auto')
        self.custom_fps = tk.StringVar(value='24')
        self.keep_audio = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value='待機中  ·  動画を選択してください')
        self.running = False
        self.last_project = None
        self.step_chips = []
        self._probe_index = 0
        self._pending_probe = None
        # Inject TkDnD into CustomTkinter's existing Tcl root.
        # We do not create a second Tk root (important for file drops on Windows).
        try:
            self.TkdndVersion = TkinterDnD._require(self)
        except Exception as exc:
            messagebox.showerror(
                'ドラッグ＆ドロップを初期化できません',
                'TkDnDの読み込みに失敗しました。\n'
                'python -m pip install --upgrade tkinterdnd2 を確認してください。\n\n'
                + str(exc), parent=self)
            self.destroy()
            raise SystemExit(1) from exc
        self._layout()
        try:
            self._register_drop_targets()
        except (tk.TclError, AttributeError, RuntimeError) as exc:
            messagebox.showerror(
                'ドラッグ＆ドロップの登録に失敗しました',
                'D&Dの受け付け設定に失敗しました。\n'
                'tkinterdnd2を再インストールし、通常権限で起動してください。\n\n'
                + str(exc), parent=self)
            self.destroy()
            raise SystemExit(1) from exc
        self.video.trace_add('write', self._on_video_change)
        self.protocol('WM_DELETE_WINDOW', self._close)

    def _label(self, parent, text, size=12, color='secondary', weight='normal', **kwargs):
        return ctk.CTkLabel(
            parent, text=text,
            text_color=COLORS.get(color, color),
            font=ctk.CTkFont(family='Yu Gothic UI', size=size, weight=weight),
            **kwargs
        )

    def _panel(self, parent, **kwargs):
        return ctk.CTkFrame(
            parent, fg_color=COLORS['panel'],
            border_width=1, border_color=COLORS['outline'],
            corner_radius=16, **kwargs
        )

    def _layout(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self, fg_color='transparent')
        top.grid(row=0, column=0, sticky='ew', padx=25, pady=(19, 10))
        top.grid_columnconfigure(1, weight=1)
        mark = ctk.CTkFrame(top, width=47, height=47, corner_radius=13,
                            fg_color=COLORS['accent'])
        mark.grid(row=0, column=0, rowspan=2, padx=(0, 15))
        mark.grid_propagate(False)
        self._label(mark, '▶', size=22, color='accent_text', weight='bold').place(relx=.5, rely=.5, anchor='center')
        self._label(top, APP_BASE_TITLE, size=22, color='text', weight='bold').grid(
            row=0, column=1, sticky='w')
        self._label(top, '動画を、何度でも使える透過アニメ素材へ。', size=12).grid(
            row=1, column=1, sticky='w', pady=(1, 0))
        version = ctk.CTkFrame(top, fg_color=COLORS['panel'], corner_radius=11,
                               border_width=1, border_color=COLORS['outline'])
        version.grid(row=0, column=2, rowspan=2, sticky='e', padx=(10, 0))
        self._label(version, f'Ver.{VERSION}', size=12, color='accent', weight='bold').pack(padx=15, pady=10)

        body = ctk.CTkScrollableFrame(self, fg_color='transparent', corner_radius=0)
        body.grid(row=1, column=0, sticky='nsew', padx=(13, 16), pady=(0, 4))
        body.grid_columnconfigure(0, weight=1)

        # File inputs
        input_card = self._panel(body)
        input_card.grid(row=0, column=0, sticky='ew', padx=12, pady=(5, 12))
        input_card.grid_columnconfigure(0, weight=1)
        self._label(input_card, '01   入出力ファイル', 15, 'text', 'bold').grid(
            row=0, column=0, sticky='w', padx=20, pady=(17, 6))
        self._path_input(input_card, 1, '変換元の動画', self.video, self.choose_movie, '動画を選択')
        self._path_input(input_card, 2, '保存先のフォルダー', self.destination,
                         self.choose_folder, '保存先を選択')
        self.drop_area = ctk.CTkFrame(
            input_card, fg_color=COLORS['panel_deep'],
            border_width=2, border_color=COLORS['outline'], corner_radius=10,
            height=60)
        self.drop_area.grid(row=3, column=0, sticky='ew', padx=20, pady=(5, 4))
        self.drop_area.grid_propagate(False)
        self.drop_label = self._label(
            self.drop_area, '↓  ここに動画ファイルをドラッグ＆ドロップ',
            12, 'accent', 'bold')
        self.drop_label.place(relx=.5, rely=.5, anchor='center')
        self._label(input_card, 'MP4 / MOV / MKV / AVI / WebM / M4V  ·  1ファイルずつ読み込み',
                    11).grid(row=4, column=0, sticky='w', padx=20, pady=(5, 17))

        # Output configuration
        config_card = self._panel(body)
        config_card.grid(row=1, column=0, sticky='ew', padx=12, pady=(0, 12))
        config_card.grid_columnconfigure(0, weight=1)
        self._label(config_card, '02   出力設定', 15, 'text', 'bold').grid(
            row=0, column=0, sticky='w', padx=20, pady=(16, 8))
        config_line = ctk.CTkFrame(config_card, fg_color='transparent')
        config_line.grid(row=1, column=0, sticky='ew', padx=19)
        config_line.grid_columnconfigure(4, weight=1)
        self._label(config_line, 'フレームレート', 12, 'secondary', 'bold').grid(
            row=0, column=0, sticky='w', padx=(0, 18))
        ctk.CTkRadioButton(
            config_line, text='元動画を維持（推奨）', variable=self.fps_mode,
            value='auto', command=self._fps_toggled,
            font=ctk.CTkFont(family='Yu Gothic UI', size=12),
            fg_color=COLORS['accent'], hover_color=COLORS['accent_hover'],
            border_color=COLORS['secondary'], text_color=COLORS['text']
        ).grid(row=0, column=1, sticky='w', padx=(0, 18))
        ctk.CTkRadioButton(
            config_line, text='手動', variable=self.fps_mode, value='manual',
            command=self._fps_toggled,
            font=ctk.CTkFont(family='Yu Gothic UI', size=12),
            fg_color=COLORS['accent'], hover_color=COLORS['accent_hover'],
            border_color=COLORS['secondary'], text_color=COLORS['text']
        ).grid(row=0, column=2, sticky='w', padx=(0, 8))
        self.fps_entry = ctk.CTkEntry(
            config_line, width=75, height=32, textvariable=self.custom_fps,
            fg_color=COLORS['panel_deep'], border_color=COLORS['outline'],
            text_color=COLORS['text'], justify='center'
        )
        self.fps_entry.grid(row=0, column=3, sticky='w')
        self._label(config_line, 'fps', 11).grid(row=0, column=4, sticky='w', padx=(7, 0))
        self._fps_toggled()

        audio_line = ctk.CTkFrame(config_card, fg_color='transparent')
        audio_line.grid(row=2, column=0, sticky='ew', padx=20, pady=(16, 16))
        ctk.CTkSwitch(
            audio_line, text='元動画の音声を保持する', variable=self.keep_audio,
            font=ctk.CTkFont(family='Yu Gothic UI', size=12),
            text_color=COLORS['text'], progress_color=COLORS['accent'],
            button_color=COLORS['text'], button_hover_color='#DBE8F8'
        ).pack(side='left')
        self._label(audio_line, 'QTRLE / ARGB  ·  透過MOVを書き出します', 11).pack(side='right')

        # Pipeline stages
        flow_card = self._panel(body)
        flow_card.grid(row=2, column=0, sticky='ew', padx=12, pady=(0, 12))
        for flow_column in (0, 2, 4):
            flow_card.grid_columnconfigure(flow_column, weight=1)
        self._label(flow_card, '03   自動処理フロー', 15, 'text', 'bold').grid(
            row=0, column=0, columnspan=5, sticky='w', padx=20, pady=(16, 12))
        for idx, (step, detail) in enumerate([
            ('PNG連番に分解', 'FFmpeg'),
            ('背景を一括除去', 'Photoshop + JSX'),
            ('透過MOVを生成', 'QTRLE / ARGB')
        ]):
            col = idx * 2
            stage = ctk.CTkFrame(flow_card, fg_color=COLORS['panel_deep'],
                                 corner_radius=11, border_width=1,
                                 border_color=COLORS['outline'])
            stage.grid(row=1, column=col, sticky='ew', padx=(20 if idx == 0 else 5,
                                                             20 if idx == 2 else 5), pady=(0, 17))
            chip = self._label(stage, f'{idx+1:02d}  {step}', 12, 'text', 'bold')
            chip.pack(anchor='center', padx=9, pady=(12, 2))
            self._label(stage, detail, 10).pack(anchor='center', pady=(0, 12))
            self.step_chips.append((stage, chip))
            if idx != 2:
                self._label(flow_card, '→', 19, 'accent', 'bold').grid(row=1, column=col+1)

        action_row = ctk.CTkFrame(body, fg_color='transparent')
        action_row.grid(row=3, column=0, sticky='ew', padx=12, pady=(0, 12))
        action_row.grid_columnconfigure(0, weight=1)
        self.go = ctk.CTkButton(
            action_row, text='一括変換を開始   →', height=52, corner_radius=12,
            fg_color=COLORS['accent'], hover_color=COLORS['accent_hover'],
            text_color=COLORS['accent_text'],
            font=ctk.CTkFont(family='Yu Gothic UI', size=16, weight='bold'),
            command=self.start
        )
        self.go.grid(row=0, column=0, sticky='ew', padx=(0, 10))
        self.open_button = ctk.CTkButton(
            action_row, text='保存先を開く', width=138, height=52,
            corner_radius=12, fg_color=COLORS['panel'],
            hover_color='#213550', border_width=1,
            border_color=COLORS['outline'], text_color=COLORS['text'],
            font=ctk.CTkFont(family='Yu Gothic UI', size=12, weight='bold'),
            command=self.open_last_project, state='disabled'
        )
        self.open_button.grid(row=0, column=1)

        log_card = self._panel(body)
        log_card.grid(row=4, column=0, sticky='ew', padx=12, pady=(0, 13))
        log_card.grid_columnconfigure(0, weight=1)
        header = ctk.CTkFrame(log_card, fg_color='transparent')
        header.grid(row=0, column=0, sticky='ew', padx=18, pady=(13, 7))
        header.grid_columnconfigure(0, weight=1)
        self._label(header, '処理状況 / ログ', 13, 'text', 'bold').grid(row=0, column=0, sticky='w')
        self._label(header, 'LIVE STATUS', 10, 'accent', 'bold').grid(row=0, column=1, sticky='e')
        self._label(log_card, '', 11, 'secondary', textvariable=self.status, anchor='w').grid(
            row=1, column=0, sticky='ew', padx=20, pady=(0, 5))
        self.progress = ctk.CTkProgressBar(
            log_card, height=7, corner_radius=4,
            fg_color=COLORS['outline'], progress_color=COLORS['accent'])
        self.progress.grid(row=2, column=0, sticky='ew', padx=20, pady=(0, 10))
        self.progress.set(0)
        self.log = ctk.CTkTextbox(
            log_card, height=112, fg_color=COLORS['panel_deep'],
            text_color='#C8D6E8', border_width=1,
            border_color=COLORS['outline'], corner_radius=9,
            font=ctk.CTkFont(family='Consolas', size=11), wrap='word'
        )
        self.log.grid(row=3, column=0, sticky='ew', padx=18, pady=(0, 16))
        self.log.configure(state='disabled')

        footer = ctk.CTkFrame(self, fg_color=COLORS['panel_deep'], corner_radius=0, height=34)
        footer.grid(row=2, column=0, sticky='ew')
        self._label(footer, 'ExplorerからD&D対応  /  Photoshop必須  /  元FPSを維持  /  中間ファイル保存',
                    10).pack(anchor='w', padx=29, pady=8)

    def _path_input(self, parent, row_number, caption, variable, callback, button_text):
        block = ctk.CTkFrame(parent, fg_color='transparent')
        block.grid(row=row_number, column=0, sticky='ew', padx=20, pady=(2, 7))
        block.grid_columnconfigure(0, weight=1)
        self._label(block, caption, 12, 'secondary', 'bold').grid(row=0, column=0, sticky='w', pady=(0, 5))
        line = ctk.CTkFrame(block, fg_color='transparent')
        line.grid(row=1, column=0, sticky='ew')
        line.grid_columnconfigure(0, weight=1)
        entry = ctk.CTkEntry(
            line, textvariable=variable, height=38,
            fg_color=COLORS['panel_deep'], border_color=COLORS['outline'],
            text_color=COLORS['text'],
            font=ctk.CTkFont(family='Yu Gothic UI', size=12)
        )
        entry.grid(row=0, column=0, sticky='ew')
        if row_number == 1:
            self.video_entry = entry
        ctk.CTkButton(
            line, text=button_text, width=126, height=38, corner_radius=9,
            fg_color='#253A54', hover_color='#345170',
            text_color=COLORS['text'],
            font=ctk.CTkFont(family='Yu Gothic UI', size=12, weight='bold'),
            command=callback
        ).grid(row=0, column=1, padx=(10, 0))

    def _fps_toggled(self):
        self.fps_entry.configure(state='normal' if self.fps_mode.get() == 'manual' else 'disabled')

    def _show_step(self, current):
        """0: idle; 1-3: current stage; 4: all completed."""
        def apply():
            for idx, (card, chip) in enumerate(self.step_chips):
                stage = idx + 1
                if current > stage:
                    card.configure(border_color='#237C74')
                    chip.configure(text_color=COLORS['accent'])
                elif current == stage:
                    card.configure(border_color=COLORS['accent'])
                    chip.configure(text_color=COLORS['text'])
                else:
                    card.configure(border_color=COLORS['outline'])
                    chip.configure(text_color=COLORS['text'])
        self.after(0, apply)

    def _register_drop_targets(self):
        """Accept Explorer file drops on the drop zone and video path entry."""
        targets = [self, self.drop_area, self.drop_label, self.video_entry]
        # CustomTkinter entries contain a native Tk entry that receives mouse events.
        if hasattr(self.video_entry, '_entry'):
            targets.append(self.video_entry._entry)
        if hasattr(self.drop_area, '_canvas'):
            targets.append(self.drop_area._canvas)
        for widget in targets:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind('<<Drop>>', self._on_drop_video)
            widget.dnd_bind('<<DropEnter>>', self._on_drag_enter)
            widget.dnd_bind('<<DropLeave>>', self._on_drag_leave)

    def _on_drag_enter(self, event):
        if not self.running:
            self.drop_area.configure(border_color=COLORS['accent'])
        return 'copy'

    def _on_drag_leave(self, event):
        self.drop_area.configure(border_color=COLORS['outline'])
        return 'copy'

    def _on_drop_video(self, event):
        self.drop_area.configure(border_color=COLORS['outline'])
        if self.running:
            self.write_log('処理中のため別の動画は読み込めません。')
            return 'refuse_drop'
        try:
            paths = [Path(item) for item in self.tk.splitlist(event.data)]
        except tk.TclError:
            self.write_log('ドロップされたファイル名を解釈できませんでした。')
            return 'refuse_drop'
        supported = [p for p in paths if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS]
        if not supported:
            self.write_log('読み込み不可：対応する動画ファイルをドロップしてください。')
            return 'refuse_drop'
        if len(supported) > 1:
            self.write_log(f'{len(supported)}個の動画がドロップされました。先頭の1個を読み込みます。')
        self.video.set(str(supported[0]))
        return 'copy'

    def _on_video_change(self, *_):
        self._probe_index += 1
        if self._pending_probe is not None:
            self.after_cancel(self._pending_probe)
        self._pending_probe = self.after(250, self._probe_selected_video)

    def _probe_selected_video(self):
        self._pending_probe = None
        path = Path(self.video.get().strip().strip('"')).expanduser()
        if self.running or not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            return
        probe_id = self._probe_index
        self.write_log('動画を読み込み：' + path.name)
        self.status.set('動画を読み込みました  ·  ' + path.name)
        threading.Thread(target=self._probe_video_geometry,
                         args=(path, probe_id), daemon=True).start()

    def _probe_video_geometry(self, path, probe_id):
        try:
            ffmpeg = find_ffmpeg()
            if not ffmpeg:
                raise RuntimeError('FFmpegが見つかりません。')
            details = video_geometry(ffmpeg, path)
            text = geometry_message(details)
        except Exception as exc:
            text = '入力アスペクトを取得できません：' + str(exc)
        def report():
            if probe_id == self._probe_index and not self.running:
                self.write_log(text)
        self.after(0, report)

    def choose_movie(self):
        path = filedialog.askopenfilename(parent=self, title='動画を選択', filetypes=VIDEO_FILTERS)
        if path and not self.running:
            self.video.set(path)

    def choose_folder(self):
        path = filedialog.askdirectory(parent=self, title='保存先フォルダー')
        if path:
            self.destination.set(path)

    def write_log(self, line):
        def append():
            self.log.configure(state='normal')
            self.log.insert('end', line + '\n')
            self.log.see('end')
            self.log.configure(state='disabled')
        self.after(0, append)

    def set_status(self, text):
        self.after(0, lambda: self.status.set(text))
        self.write_log(text)

    def _set_progress_mode(self, mode, value=0):
        """Tk changes must be scheduled onto the UI thread."""
        def apply():
            self.progress.stop()
            self.progress.configure(mode=mode)
            if mode == 'determinate':
                self.progress.set(value)
            else:
                self.progress.start()
        self.after(0, apply)

    def _watch_photoshop(self, progress_path, total, stop_event):
        """Poll the JSX heartbeat without blocking Photoshop or the UI."""
        last_seen = (-1, -1)
        next_log = 10
        while not stop_event.is_set():
            item = read_photoshop_progress(progress_path)
            if item is not None:
                done = min(item['processed'], total)
                current = min(item['current'], total)
                key = (current, done)
                if key != last_seen:
                    last_seen = key
                    pct = int((100 * done) / total) if total else 0
                    if done >= total:
                        msg = f'ステップ2/3  ·  Photoshop背景除去完了  {done}/{total}枚（100%）'
                    else:
                        msg = (f'ステップ2/3  ·  Photoshop背景除去中  '
                               f'{max(current, 1)}/{total}枚目を処理中  '
                               f'［完了 {done}枚・{pct}%］')
                    self.after(0, lambda message=msg, fraction=done / total:
                               (self.status.set(message), self.progress.set(fraction)))
                    if done >= next_log:
                        self.write_log(f'背景除去の進捗：{done}/{total}枚（{pct}%）')
                        next_log = ((done // 10) + 1) * 10
            stop_event.wait(0.3)
        # The final heartbeat can arrive just before the process exits.
        item = read_photoshop_progress(progress_path)
        if item is not None and item['processed'] == total:
            self.after(0, lambda: (self.status.set(
                f'ステップ2/3  ·  Photoshop背景除去完了  {total}/{total}枚（100%）'),
                self.progress.set(1)))

    def popup_on_main(self, method, *args):
        gate = threading.Event()
        result = [None]
        def show():
            try:
                result[0] = method(*args)
            finally:
                gate.set()
        self.after(0, show)
        gate.wait()
        return result[0]

    def manual_photoshop(self, jsx, failure):
        message = (
            'Photoshopの自動起動に失敗しました。\n\n'
            'Photoshopで［ファイル］→［スクリプト］→［参照］を開いて、\n'
            f'次のJSXを実行してください。\n\n{jsx}\n\n'
            '背景除去が完了してから［はい］を押すとMOV化を続行します。\n'
            'キャンセルする場合は［いいえ］を押してください。\n\n'
            f'自動実行エラー：{failure[:350]}'
        )
        return self.popup_on_main(messagebox.askyesno, 'Photoshop手動実行に切り替え', message)

    def start(self):
        if self.running:
            return
        movie = Path(self.video.get().strip().strip('"')).expanduser()
        dest = Path(self.destination.get().strip().strip('"')).expanduser()
        if not movie.is_file():
            messagebox.showerror('入力エラー', '有効な動画ファイルを指定してください。', parent=self)
            return
        if not dest.is_dir():
            messagebox.showerror('入力エラー', '保存先フォルダーが存在しません。', parent=self)
            return
        fps_override = None
        if self.fps_mode.get() == 'manual':
            try:
                fps_override = float(self.custom_fps.get())
                if not 0.1 <= fps_override <= 240:
                    raise ValueError()
            except ValueError:
                messagebox.showerror('入力エラー', 'FPSは0.1～240で指定してください。', parent=self)
                return
        self.running = True
        self.go.configure(state='disabled', text='処理中です…')
        self.open_button.configure(state='disabled')
        self.progress.configure(mode='indeterminate')
        self.progress.start()
        self._show_step(1)
        self.set_status('処理を開始します')
        threading.Thread(
            target=self.worker,
            args=(movie.resolve(), dest.resolve(), fps_override, self.keep_audio.get()),
            daemon=True
        ).start()

    def finish(self, error=None, output=None):
        def done():
            self.progress.stop()
            self.progress.configure(mode='determinate')
            self.progress.set(0 if error else 1)
            self.go.configure(state='normal', text='一括変換を開始   →')
            self.running = False
            if error:
                self.status.set('エラー  ·  下のログを確認してください')
                messagebox.showerror('処理を完了できませんでした', str(error)[-2800:], parent=self)
            else:
                self._show_step(4)
                self.last_project = output.parent
                self.open_button.configure(state='normal')
                self.status.set(f'完了  ·  {output.name}')
                messagebox.showinfo('変換完了', '透過MOVを作成しました。\n' + str(output), parent=self)
                self.open_last_project()
        self.after(0, done)

    def open_last_project(self):
        if not self.last_project:
            return
        try:
            os.startfile(str(self.last_project))
        except (AttributeError, OSError):
            messagebox.showinfo('保存先', str(self.last_project), parent=self)

    def _close(self):
        if self.running:
            messagebox.showwarning('処理中', '処理が終了してから閉じてください。', parent=self)
            return
        self.destroy()

    def worker(self, movie, dest, override_fps, audio):
        project = None
        try:
            ffmpeg = find_ffmpeg()
            if not ffmpeg:
                raise RuntimeError(
                    'FFmpegが見つかりません。EXE版なら同梱が必要です。\n'
                    '開発版の場合はffmpeg.exeを同じフォルダーに置いてください。'
                )
            fps = override_fps or source_fps(ffmpeg, movie)
            if not fps:
                raise RuntimeError('元動画のFPSを読み取れませんでした。手動FPS指定で再実行してください。')
            if float(fps) <= 0:
                raise RuntimeError('FPSが不正です。')
            project = new_project_folder(dest, movie.stem)
            originals = project / 'original_frames'
            alpha = originals / 'bg_removed'
            originals.mkdir()
            alpha.mkdir()

            self._show_step(1)
            self.set_status(f'ステップ1/3  ·  PNG連番を抽出中（{fps}fps）')
            command([
                ffmpeg, '-hide_banner', '-y', '-i', str(movie), '-vsync', '0',
                '-start_number', '1', str(originals / FRAME_PATTERN)
            ], 'PNG連番分解')
            count = frame_count(originals)
            if count < 1:
                raise RuntimeError('フレームを抽出できませんでした。')
            self.write_log(f'抽出完了：{count}枚 / 作業フォルダー：{project}')

            self._show_step(2)
            self.set_status(f'ステップ2/3  ·  Photoshopで背景一括除去中（全{count}枚）')
            jsx, resultlog, progressfile = write_jsx(project, originals, alpha)
            self._set_progress_mode('determinate', 0)
            observer_stop = threading.Event()
            observer = threading.Thread(
                target=self._watch_photoshop,
                args=(progressfile, count, observer_stop),
                daemon=True
            )
            observer.start()
            try:
                try:
                    run_photoshop(jsx, project)
                except Exception as exc:
                    self.write_log('Photoshop自動実行エラー：' + str(exc))
                    if not self.manual_photoshop(jsx, str(exc)):
                        raise RuntimeError('Photoshop処理を中止しました.\nJSX: ' + str(jsx)) from exc
            finally:
                observer_stop.set()
                observer.join(timeout=1.5)

            if not resultlog.is_file():
                raise RuntimeError(
                    'Photoshopの完了ログがありません。\nPhotoshopでJSXを実行し、'
                    '正常終了していることを確認してください。\n' + str(jsx)
                )
            result_content = resultlog.read_text(encoding='utf-8-sig', errors='replace')
            self.write_log('Photoshop処理結果：' + result_content.replace('\n', ' / '))
            error_match = re.search(r'^errors=(\d+)\s*$', result_content, re.M)
            if error_match and int(error_match.group(1)) > 0:
                raise RuntimeError('Photoshopで背景除去に失敗した画像があります。\n' + result_content[-1600:])
            alpha_count = frame_count(alpha)
            if alpha_count != count:
                raise RuntimeError(
                    f'枚数不一致：抽出{count}枚、背景除去{alpha_count}枚。\n'
                    'Photoshopの処理を確認してください。'
                )
            # An ARGB video is not necessarily transparent; verify PNG alpha.
            candidates = [alpha / f'frame_{k:06d}.png'
                          for k in sorted({1, max(1, count // 2), count})]
            if not any(transparency_present(ffmpeg, image) for image in candidates if image.exists()):
                raise RuntimeError(
                    '背景除去済みPNGに透明な画素を確認できません。\n'
                    'Photoshopで透明部分（市松模様）があるか確認してください。\n'
                    '中間PNGは保存されています。'
                )

            self._show_step(3)
            self.set_status('ステップ3/3  ·  透過MOV（QTRLE / ARGB）を生成中')
            self._set_progress_mode('indeterminate')
            output = project / (display_name(movie.stem) + '_alpha.mov')
            encode_mov(ffmpeg, alpha, movie, output, fps, count, audio)
            if not output.is_file() or output.stat().st_size == 0:
                raise RuntimeError('MOVファイルが正常に生成されませんでした。')
            self.write_log('完成：' + str(output))
            self.finish(output=output)
        except Exception as exc:
            self.write_log('エラー：' + str(exc))
            if project:
                self.write_log('中間ファイル：' + str(project))
            self.finish(error=exc)


if __name__ == '__main__':
    BGAlphaStudio().mainloop()

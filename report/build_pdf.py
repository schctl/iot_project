"""Fill report_template.md from report/generated/values.json and render the A4 PDF (ReportLab).

  python report/make_figures.py && python report/build_pdf.py [--pages]

Outputs report/report.md (filled Markdown) and report/report.pdf; --pages also renders every page to
report/_pages/page-NN.png for layout checks. Any {{placeholder}} without a measured value aborts the build.
"""
import json
import re
import sys
from pathlib import Path

import matplotlib
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame, Image, KeepTogether, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle, XPreformatted)
from reportlab.platypus.flowables import HRFlowable
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = Path(__file__).resolve().parents[1]
REP = ROOT / "report"
RES = ROOT / "results"
V = {}

# ---------------- fonts and styles ----------------
mono_dir = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
pdfmetrics.registerFont(TTFont("DejaVuMono", str(mono_dir / "DejaVuSansMono.ttf")))
MONO = "DejaVuMono"


def heading_font():
    """Libertinus Serif Bold for headings. The system font is OpenType-CFF, which ReportLab cannot embed, so it is
    converted once to TrueType outlines (fontTools cu2qu) into report/generated/fonts/."""
    src = Path("/usr/share/fonts/libertinus/LibertinusSerif-Bold.otf")
    dst = REP / "generated" / "fonts" / "LibertinusSerif-Bold.ttf"
    if not dst.exists():
        from fontTools.pens.cu2quPen import Cu2QuPen
        from fontTools.pens.ttGlyphPen import TTGlyphPen
        from fontTools.ttLib import TTFont as FTFont, newTable
        f = FTFont(src)
        gs = f.getGlyphSet()
        glyf = newTable("glyf")
        glyf.glyphOrder, glyf.glyphs = f.getGlyphOrder(), {}
        for name in f.getGlyphOrder():
            pen = TTGlyphPen(gs)
            gs[name].draw(Cu2QuPen(pen, max_err=1.0, reverse_direction=True))
            glyf[name] = pen.glyph()
        f["glyf"], f["loca"] = glyf, newTable("loca")
        maxp = f["maxp"]
        maxp.tableVersion = 0x00010000
        for a in ("maxZones", "maxTwilightPoints", "maxStorage", "maxFunctionDefs", "maxInstructionDefs",
                  "maxStackElements", "maxSizeOfInstructions", "maxComponentElements"):
            setattr(maxp, a, 0)
        maxp.maxZones = 1
        for tag in ("CFF ", "VORG"):
            if tag in f:
                del f[tag]
        f.sfntVersion = "\x00\x01\x00\x00"
        f["post"].formatType = 3.0
        dst.parent.mkdir(parents=True, exist_ok=True)
        f.save(dst)
    pdfmetrics.registerFont(TTFont("LibertinusSerif-Bold", str(dst)))
    return "LibertinusSerif-Bold"


HEAD_FONT = heading_font()
CODE_PT = 8.3
S = {
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9.4, leading=12.6, spaceAfter=5, alignment=TA_LEFT),
    "bullet": ParagraphStyle("bullet", fontName="Helvetica", fontSize=9.4, leading=12.4, leftIndent=12, bulletIndent=2,
                             spaceAfter=2.5),
    "h1": ParagraphStyle("h1", fontName=HEAD_FONT, fontSize=13.5, leading=16, spaceBefore=10, spaceAfter=2,
                         keepWithNext=1),
    "h2": ParagraphStyle("h2", fontName=HEAD_FONT, fontSize=11, leading=13.5, spaceBefore=8, spaceAfter=2,
                         keepWithNext=1),
    "capc": ParagraphStyle("capc", fontName="Helvetica-Oblique", fontSize=8.3, leading=10.5, alignment=1,
                           spaceBefore=3, spaceAfter=9),
    "tcap": ParagraphStyle("tcap", fontName="Helvetica-Oblique", fontSize=8.3, leading=10.5, alignment=1,
                           spaceBefore=4, spaceAfter=4),
    "caption": ParagraphStyle("caption", fontName="Helvetica-Oblique", fontSize=8.3, leading=10.5, spaceBefore=2,
                              spaceAfter=8),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8.3, leading=10.2),
    "cellb": ParagraphStyle("cellb", fontName="Helvetica-Bold", fontSize=8.3, leading=10.2),
    "code": ParagraphStyle("code", fontName=MONO, fontSize=CODE_PT, leading=CODE_PT * 1.22),
    "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=19, leading=23, spaceAfter=6),
    "subtitle": ParagraphStyle("subtitle", fontName="Helvetica", fontSize=10.5, leading=14, spaceAfter=14),
    "label": ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=9.4, leading=12.6),
    "toc1": ParagraphStyle("toc1", fontName="Helvetica", fontSize=9.4, leading=12.5, leftIndent=0),
    "toc2": ParagraphStyle("toc2", fontName="Helvetica", fontSize=8.8, leading=11.5, leftIndent=12),
}
PAGE_W, PAGE_H = A4
MARGIN = 20 * mm
FRAME_W = PAGE_W - 2 * MARGIN

# ---------------- tables built from values ----------------


def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def need(*keys):
    missing = [k for k in keys if k not in V]
    if missing:
        raise SystemExit(f"missing measured values: {missing}")


def t_dataset():
    ds = json.loads((RES / "dataset_summary.json").read_text())
    c = ds["counts_by_topology_leak"]
    per = ", ".join(f"{k}: LO {c[k]['LO']} / BR {c[k]['BR']}" for k in ("NL", "CC", "LC", "GL", "OL"))
    cond = ", ".join(f"{k.replace('_', ' + ')} ({v})" for k, v in ds["counts_by_condition"].items())
    return md_table(["Item", "Measured"], [
        ["Hydrophone files", f"{V['ds_files']} ({V['ds_recordings']} leak/no-leak + {V['ds_background']} background-noise)"],
        ["Experiments (each with H1 + H2)", V["ds_experiments"]],
        ["Files per class and topology", per],
        ["Conditions (flow + background, files)", cond],
        ["Binary balance (files)", f"leak {V['ds_leak_files']}, no-leak {V['ds_noleak_files']}"],
        ["Duration (s)", f"{V['ds_dur_min']}–{V['ds_dur_max']}, median {V['ds_dur_median']}"],
        ["Sample format", "headerless little-endian int32, low 16 bits always zero"],
        ["Sample rate", "8000 Hz (per the dataset's reader; cannot be checked from headerless files)"],
    ])


def t_protocols():
    rows = [["E1", "LO, all conditions", "BR", "e1_lo_br"], ["E1", "BR, all conditions", "LO", "e1_br_lo"],
            ["E1 (no low cut)", "LO", "BR", "e1nl_lo_br"], ["E1 (no low cut)", "BR", "LO", "e1nl_br_lo"],
            ["E1b", "LO, 5 of 6 conditions", "LO, held-out condition", "e1b_lo"],
            ["E1b", "BR, 5 of 6 conditions", "BR, held-out condition", "e1b_br"],
            ["E2", "first 70 % of every recording", "last 30 %", "e2"]]
    out = []
    for p, tr, te, k in rows:
        need(f"{k}_ba")
        vote = V.get(f"{k}_vote_ba", "–")
        out.append([p, tr, te, f"{V[k + '_ba']} ± {V[k + '_ba_sd']}", vote])
    return md_table(["Protocol", "Train", "Test", "Window BA (mean ± s.d.)", "Recording-vote BA"], out)


def t_per_condition():
    pc = V["e1b_per_condition"]
    names = {"0.18_N": "0.18 L/s, noise", "0.47_N": "0.47 L/s, noise", "ND_N": "no demand, noise",
             "ND_NN": "no demand, quiet", "T_N": "transient, noise", "T_NN": "transient, quiet"}
    rows = []
    for c in V["e1b_conditions"]:
        lo, br = pc[f"LO_{c}"], pc[f"BR_{c}"]
        rows.append([names.get(c, c), lo["acc"], lo["no_leak"], lo["leak"], br["acc"], br["no_leak"], br["leak"]])
    return md_table(["Held-out condition", "LO acc.", "LO recall no-leak", "LO recall leak", "BR acc.",
                     "BR recall no-leak", "BR recall leak"], rows)


def t_e4():
    need("e4_fs4000_lo_ba", "e4_fs8000_lo_ba")
    return md_table(["Input", "LO BA (E1b)", "BR BA (E1b)"], [
        ["4 kHz (resampled), 1024-sample windows", f"{V['e4_fs4000_lo_ba']} ± {V['e4_fs4000_lo_ba_sd']}",
         f"{V['e4_fs4000_br_ba']} ± {V['e4_fs4000_br_ba_sd']}"],
        ["8 kHz (original), 2048-sample windows", f"{V['e4_fs8000_lo_ba']} ± {V['e4_fs8000_lo_ba_sd']}",
         f"{V['e4_fs8000_br_ba']} ± {V['e4_fs8000_br_ba_sd']}"]])


def t_e5():
    need("host_test_ba_f32", "dev_ba_f32")
    cm = lambda m: f"TN {m[0][0]}, FP {m[0][1]}, FN {m[1][0]}, TP {m[1][1]}"
    return md_table(["Implementation", "Platform", "Test BA", "Confusion (no-leak = negative)"], [
        ["TFLite float32", "host", V["host_test_ba_f32"], "–"],
        ["TFLite int8 (reference kernels)", "host", V["host_test_ba_i8"], "–"],
        ["Hand-written C float32", "Nano (HIL, serial)", V["dev_ba_f32"], cm(V["dev_cm_f32"])],
        ["Hand-written C int8", "Nano (HIL, serial)", V["dev_ba_i8"], cm(V["dev_cm_i8"])]])


def t_latency():
    n = V["nano"]
    row = lambda name, k: [name, n[f"ble0_opt0_{k}_med"], n[f"ble0_opt1_{k}_med"], n[f"ble0_opt1_{k}_p99"],
                           n.get(f"prev_ble1_opt1_{k}_med", "–")]
    rows = [row("Features total", "feat_us"), row("  of which FFT (arm_rfft_fast_f32)", "fft_us"),
            row("MLP float32", "mlp_f32_us"), row("MLP int8 (incl. input quantization)", "mlp_i8_us")]
    rows.append(["Profile: window prep / magnitude + log / bands", f"{n['ble0_opt0_prof_prep_us']} / {n['ble0_opt0_prof_maglog_us']} / {n['ble0_opt0_prof_band_std_us']}",
                 f"{n['ble0_opt1_prof_prep_us']} / {n['ble0_opt1_prof_maglog_us']} / {n['ble0_opt1_prof_band_std_us']}", "–",
                 f"{n['prev_ble1_opt1_prof_prep_us']} / {n['prev_ble1_opt1_prof_maglog_us']} / {n['prev_ble1_opt1_prof_band_std_us']}"])
    t = md_table(["Stage (ms per window)", "Nano baseline, BLE off (median)", "Nano optimized, BLE off (median)",
                  "Nano optimized, BLE off (p99)", "Nano optimized, BLE on (median, earlier build*)"], rows)
    t2 = md_table(["Host reference (µs per window, batch 1, " + str(V["host_threads"]) + " thread)", "median"], [
        ["numpy features", V["host_feat_us"]], ["TFLite float32 (default kernels)", V["host_f32_us"]],
        ["TFLite int8 (default kernels)", V["host_i8_us"]], ["TFLite float32 (reference kernels)", V["host_f32_ref_us"]],
        ["TFLite int8 (reference kernels)", V["host_i8_ref_us"]]])
    note = ("\n\n*BLE-on settings are from the earlier firmware build (`results/nano_bench_prev_build.json`): the "
            "feature code is identical, the MLP code was later reorganized. They could not be re-measured on the final "
            "build because the board reset during those runs.")
    return t + note + "\n\n" + t2


def t_memory():
    return md_table(["Build / item", "Flash (B)", "Static RAM (B)"], [
        ["Empty sketch (mbed OS + USB serial)", V["flash_empty"], V["ram_empty"]],
        ["+ ArduinoBLE peripheral, one service", V["flash_ble"], V["ram_ble"]],
        ["Full firmware, stock CMSIS-DSP tables", V["flash_alltables"], V["ram_alltables"]],
        ["Full firmware, final (1024-pt tables only)", V["flash_final"], V["ram_final"]],
        ["  CMSIS FFT tables (stock → final)", f"{V['flash_cmsis_tables_stock']} → {V['flash_cmsis_tables']}", "–"],
        ["  Model weights float32 / int8", f"{V['flash_model_f32']} / {V['flash_model_i8']}", "–"],
        ["  Self-test vectors", V["flash_testvec"], "–"],
        ["  Feature tables (Hann, band edges, μ, σ)", V["flash_featuretables"], "–"],
        ["Runtime: main stack high-water / size", "–", f"{V['stack_hwm']} / {V['stack_size']}"],
        ["Runtime: heap in use (mallinfo)", "–", V["heap_in_use"]]])


def t_ble():
    k = lambda key: (V.get(f"ble_stock_{key}", "–"), V.get(f"ble_patched_{key}", "–"))
    rows = []
    for label, key in [("Real-time duration (s)", "rt_s"), ("Windows sent (one per 256 ms)", "rt_n"),
                       ("Frames sent", "rt_frames_sent"), ("Frames received by the Nano", "rt_frames_rx"),
                       ("Payload delivered to the Nano (B/s)", "rt_rx_payload_Bps"),
                       ("Required for real-time 4 kHz int16 (B/s)", "rt_required_Bps"),
                       ("Sequence errors on the Nano", "rt_seq_err"), ("CRC errors on the Nano", "rt_crc_err"),
                       ("Windows processed by the Nano", "rt_windows_done"),
                       ("Windows without result within 2 s (dropped)", "rt_dropped"),
                       ("Results later than the 256 ms period", "rt_late")]:
        a, b = k(key)
        rows.append([label, a, b])
    return md_table(["E7 real-time test", "Stock ArduinoBLE", "Patched (4 KB HCI ring)"], rows)


def t_problem():
    return md_table(["Aspect", "This project"], [
        ["What is sensed", "Pipe-wall/water acoustic pressure from a hydrophone (testbed recordings, 8 kHz; resampled to 4 kHz)"],
        ["Decision made on the device", "Binary, every 256 ms window: leak / no leak"],
        ["Real-time requirement", "Features + inference must finish within one 256 ms window"],
        ["Device constraints", f"Arduino Nano 33 BLE: Cortex-M4F at {V['core_clock_mhz']} MHz, 1 MB flash, 256 KB RAM, BLE radio, battery-class power"],
        ["What leaves the device", "The decision (a few bytes), not the 8 kB/s audio stream"],
        ["ML technique", "Hand-crafted log-spectral features + a 3-layer MLP (supervised); float32 and int8"]])


def t_complexity():
    rows = []
    for i, L in enumerate(V["complexity_layers"]):
        rows.append([f"Dense {i + 1} ({L['n_in']} → {L['n_out']}){', ReLU' if i < 2 else ''}", L["weights"], L["biases"], L["macs"]])
    rows.append(["Total", sum(L["weights"] for L in V["complexity_layers"]), sum(L["biases"] for L in V["complexity_layers"]), V["macs"]])
    t1 = md_table(["Layer", "Weights", "Biases", "MACs per inference"], rows)
    t2 = md_table(["Measure", "FP32", "INT8"], [
        ["Parameters", V["params_c"], V["params_c"]],
        ["Parameter storage (B)", V["st_fp32_all"], f"{V['st_int8']} (int8 weights + int32 biases)"],
        ["Measured MLP latency on the Nano, optimized (ms)", V["nano"]["ble0_opt1_mlp_f32_us_med"], V["nano"]["ble0_opt1_mlp_i8_us_med"]],
        ["Measured throughput = MACs ÷ latency (MMAC/s)", V["thr_ble0_opt1_mlp_f32_mmacs"], V["thr_ble0_opt1_mlp_i8_mmacs"]],
        ["Cycles per MAC at 64 MHz (incl. loop + requantization)", V["thr_ble0_opt1_mlp_f32_cpm"], V["thr_ble0_opt1_mlp_i8_cpm"]],
        ["Baseline kernels: throughput (MMAC/s)", V["thr_ble0_opt0_mlp_f32_mmacs"], V["thr_ble0_opt0_mlp_i8_mmacs"]],
        ["Meets the 256 ms window deadline?", "yes", "yes"]])
    return t1 + "\n\n" + t2


def t_platforms():
    return md_table(["Platform (course examples)", "For this 6 k-MAC model", "Energy / power class", "Deployable on a pipe?",
                     "Evaluated here"], [
        ["Microcontroller (Nano 33 BLE, nRF52840)", f"{V['total_opt_ms']} ms per window incl. features (measured)",
         "milliwatt-class, battery", "yes: small, cheap, integrated BLE", "yes (measured)"],
        ["Desktop CPU (host)", f"{V['host_i8_us']} µs int8 inference (measured)", "tens of watts", "no: mains power, size",
         "yes, as reference only"],
        ["SBC CPU (Raspberry Pi class)", "far faster than needed", "watt-class", "marginal: power, SD card, OS upkeep", "no"],
        ["Embedded GPU (NVIDIA Jetson Nano)", "massively over-provisioned; launch overhead dominates tiny MLPs", "several watts",
         "no for a battery sensor; yes for camera-scale DL", "no"],
        ["Edge TPU (Google Coral) / cloud TPU", "int8-only, needs a host; overhead dominates tiny MLPs", "watt-class (Coral module + host)",
         "no for this sensor", "no"],
        ["FPGA (Xilinx PYNQ / Kria, Vitis AI)", "custom datapath could hit µs latency", "watt-class boards; low-power FPGAs exist",
         "possible with a low-power FPGA, high design effort", "no"],
        ["MCU with NPU (e.g. Arm Ethos-U parts)", "would accelerate the MLP, which is already cheap here", "milliwatt-class",
         "yes", "no"],
        ["Cloud (stream audio)", "fast inference, but needs the radio link", "radio energy dominates", "no: 8 kB/s link "
         "not sustained even over BLE (see the BLE results)", "no"]])


def t_compression():
    return md_table(["Course technique (Module 3/4)", "Used here?", "Evidence (measured)"], [
        ["Numerical compression / quantization (INT8 PTQ)", "yes, deployed",
         f"storage {V['st_fp32_all']} → {V['st_int8']} B ({V['st_ratio']}×); BA {V['dev_ba_f32']} → {V['dev_ba_i8']}; int8 MLP {V['speedup_i8']}× faster than baseline"],
        ["Feature extraction / dimensionality reduction (role of PCA)", "yes (spectral band pooling)",
         "2048 B of audio per window → 64 features; 513 FFT bins pooled to 64 bands"],
        ["Data reduction: resampling 8 → 4 kHz", "yes", f"no accuracy change (E4: LO {V['e4_fs4000_lo_ba']} vs {V['e4_fs8000_lo_ba']})"],
        ["Pruning", "analysed, not deployed", f"BA {V['prune_0_ba']} (dense), {V['prune_50_ba']} (50 %), {V['prune_75_ba']} (75 %), {V['prune_90_ba']} (90 % pruned), no fine-tuning"],
        ["Zero-skipping / sparsity", "analysed, not deployed",
         f"ReLU outputs zero: {V['sp_l1']} % (layer 1), {V['sp_l2']} % (layer 2) → {V['sp_skip_pct']} % of MACs skippable"],
        ["Activation / function approximation", "replaced libm logf with float-only fdlibm logf",
         f"max {V['fastlog_max_ulp']} ulp error; features {V['speedup_feat']}× faster"],
        ["Fast DL kernels, dataflow, HW/SW co-design", "yes", "SIMD SMLAD int8 kernel, unrolled float kernel, folded zero-point bias, trimmed CMSIS tables"],
        ["Weight sharing, distillation, dropout", "no", "model already small (6.3 k parameters); not needed for the deadline"]])


def t_metrics():
    r = lambda k: [V[f"{k}_{m}"] for m in ("accuracy", "balanced_accuracy", "precision", "recall", "specificity", "f1", "roc_auc")]
    return md_table(["Model / platform (held-out test, leak = positive)", "Accuracy", "Balanced acc.", "Precision", "Recall",
                     "Specificity", "F1", "ROC-AUC"], [
        ["TFLite float32, host", *r("m_host_f32")], ["TFLite int8, host", *r("m_host_i8")],
        ["C float32, Nano (HIL)", *r("m_nano_f32")], ["C int8, Nano (HIL)", *r("m_nano_i8")]]) + "\n\n" + md_table(
        ["E1b, mean ± s.d. over 5 seeds", "Precision", "Recall", "Specificity", "F1"], [
        ["LO (unseen condition)", V["e1bm_lo_precision"], V["e1bm_lo_recall"], V["e1bm_lo_specificity"], V["e1bm_lo_f1"]],
        ["BR (unseen condition)", V["e1bm_br_precision"], V["e1bm_br_recall"], V["e1bm_br_specificity"], V["e1bm_br_f1"]]])


def t_co():
    return md_table(["Course outcome", "Where this project addresses it"], [
        ["CO1: basic ML algorithms and learning methods", "Supervised MLP trained by backpropagation (Adam); logistic-regression and random-forest baselines for the shift diagnosis (6.1); confusion-matrix metrics, ROC-AUC and threshold trade-off (6.5)"],
        ["CO2: requirements and characteristics of DL algorithms", "Preprocessing (resampling, FFT features, standardization), training with class weights and early stopping, storing (TFLite f32/int8), deployment (Sections 3, 5)"],
        ["CO3: implementation architecture for embedded, resource-constrained systems", "MCU platform choice and justification (4); edge vs cloud (1.2); parameter/MAC/memory budget (3.2, 6.6); compression techniques (5)"],
        ["CO4: system architectures and techniques exploiting DL", "Quantization inference engine reproducing TFLite arithmetic; fast DL kernels (SIMD); sparsity / zero-skipping and pruning analysis; memory optimization (5, 6.6)"],
        ["CO5: evaluation platforms", "Hardware-in-the-loop on the Nano vs host CPU TFLite (6.4, 6.6); BLE link evaluation (6.7); accelerator platforms compared qualitatively (4)"]])


# ---- shorter tables for the concise report ----
def t_protocols_short():
    rows = [["Train on one network, test on the other (E1)", "LO → BR", f"{V['e1_lo_br_ba']} ± {V['e1_lo_br_ba_sd']}"],
            ["", "BR → LO", f"{V['e1_br_lo_ba']} ± {V['e1_br_lo_ba_sd']}"],
            ["Same network, unseen operating condition (E1b)", "LO", f"{V['e1b_lo_ba']} ± {V['e1b_lo_ba_sd']}"],
            ["", "BR", f"{V['e1b_br_ba']} ± {V['e1b_br_ba_sd']}"],
            ["Same recordings, later 30 % of time (E2)", "both", f"{V['e2_ba']} ± {V['e2_ba_sd']}"]]
    return md_table(["How the test data was chosen", "Network", "Balanced accuracy (5 runs)"], rows)


def t_latency_short():
    n = V["nano"]
    return md_table(["Step (time per 256 ms window)", "Nano, before optimization", "Nano, after optimization", "Desktop CPU (reference)"], [
        ["Feature extraction (FFT + log + bands)", f"{n['ble0_opt0_feat_us_med']} ms", f"{n['ble0_opt1_feat_us_med']} ms", f"{V['host_feat_us']} µs"],
        ["MLP float32", f"{n['ble0_opt0_mlp_f32_us_med']} ms", f"{n['ble0_opt1_mlp_f32_us_med']} ms", f"{V['host_f32_us']} µs"],
        ["MLP int8", f"{n['ble0_opt0_mlp_i8_us_med']} ms", f"{n['ble0_opt1_mlp_i8_us_med']} ms", f"{V['host_i8_us']} µs"]])


def t_memory_short():
    return md_table(["Item", "Flash (bytes)", "RAM (bytes)"], [
        ["Empty Arduino sketch (mbed OS + USB)", V["flash_empty"], V["ram_empty"]],
        ["+ Bluetooth stack", V["flash_ble"], V["ram_ble"]],
        ["Full firmware, before trimming FFT tables", V["flash_alltables"], V["ram_alltables"]],
        ["Full firmware, final", V["flash_final"], V["ram_final"]],
        ["  of which model weights (float32 / int8)", f"{V['flash_model_f32']} / {V['flash_model_i8']}", "–"],
        ["Stack actually used at run time", "–", f"{V['stack_hwm']} of {V['stack_size']}"],
        ["Available on the nRF52840", "1,048,576", "262,144"]])


def t_ble_short():
    return md_table(["BLE test (30 s, one window every 256 ms)", "Stock BLE library", "Library with bigger receive buffer"], [
        ["Data the Nano actually received (bytes/s)", V["ble_stock_rt_rx_payload_Bps"], V["ble_patched_rt_rx_payload_Bps"]],
        ["Data rate needed for real time (bytes/s)", V["ble_stock_rt_required_Bps"], V["ble_patched_rt_required_Bps"]],
        ["Corrupted / out-of-order packets", f"{V['ble_stock_rt_crc_err']} / {V['ble_stock_rt_seq_err']}", f"{V['ble_patched_rt_crc_err']} / {V['ble_patched_rt_seq_err']}"],
        ["Windows answered within 2 s", f"{int(V['ble_stock_rt_n']) - int(V['ble_stock_rt_dropped'])} of {V['ble_stock_rt_n']}",
         f"{int(V['ble_patched_rt_n']) - int(V['ble_patched_rt_dropped'])} of {V['ble_patched_rt_n']}"]])


def t_metrics_short():
    r = lambda k: [V[f"{k}_{m}"] for m in ("accuracy", "precision", "recall", "specificity", "f1", "roc_auc")]
    return md_table(["Deployed model, held-out test (leak = positive)", "Accuracy", "Precision", "Recall", "Specificity", "F1", "ROC-AUC"], [
        ["float32, desktop (TFLite)", *r("m_host_f32")], ["int8, desktop (TFLite)", *r("m_host_i8")],
        ["float32, on the Nano", *r("m_nano_f32")], ["int8, on the Nano", *r("m_nano_i8")]])


def t_int8_short():
    return md_table(["", "float32", "int8"], [
        ["Weight storage (bytes)", V["st_fp32_all"], V["st_int8"]],
        ["Balanced accuracy on the Nano", V["dev_ba_f32"], V["dev_ba_i8"]],
        ["Same prediction as the desktop TFLite model", f"{V['hil_f32_agree']} % of windows", f"{V['hil_i8_agree']} % of windows"],
        ["MLP time on the Nano (optimized)", f"{V['nano']['ble0_opt1_mlp_f32_us_med']} ms", f"{V['nano']['ble0_opt1_mlp_i8_us_med']} ms"]])


def t_co_short():
    return md_table(["Course outcome", "Where it is covered"], [
        ["CO1: ML algorithms and learning methods", "Supervised MLP trained with backpropagation; confusion-matrix metrics (Sections 3, 6.2)"],
        ["CO2: deep-learning requirements", "Preprocessing, training, storing (TFLite) and deploying the model (Sections 3, 5)"],
        ["CO3: architecture for resource-constrained embedded systems", "MCU choice, edge vs cloud, MAC and memory budget (Sections 1, 3.2, 4)"],
        ["CO4: techniques exploiting deep learning", "int8 quantization engine, fast kernels, pruning and sparsity (Sections 5, 6.3)"],
        ["CO5: evaluation platforms", "Nano vs desktop CPU measured; other accelerators compared (Sections 4, 6.4)"]])


def t_complexity_short():
    rows = [[f"Dense {i + 1} ({L['n_in']} → {L['n_out']})", L["weights"], L["biases"], L["macs"]]
            for i, L in enumerate(V["complexity_layers"])]
    rows.append(["Total", sum(L["weights"] for L in V["complexity_layers"]), sum(L["biases"] for L in V["complexity_layers"]), V["macs"]])
    t1 = md_table(["Layer", "Weights", "Biases", "MACs per window"], rows)
    t2 = md_table(["Model size", "float32", "int8"], [["Parameters", V["params_c"], V["params_c"]],
                                                     ["Storage (bytes)", V["st_fp32_all"], f"{V['st_int8']} (int8 weights, int32 biases)"]])
    return t1 + "\n\n" + t2


def t_tests():
    return md_table(["Test", "Balanced accuracy (5 training runs)", "Recall (leaks found)", "Specificity (no-leak passed)"], [
        ["Looped network, operating condition not seen in training", f"{V['e1b_lo_ba']} ± {V['e1b_lo_ba_sd']}",
         V["e1bm_lo_recall"], V["e1bm_lo_specificity"]],
        ["Branched network, operating condition not seen in training", f"{V['e1b_br_ba']} ± {V['e1b_br_ba_sd']}",
         V["e1bm_br_recall"], V["e1bm_br_specificity"]],
        ["Trained on one network, tested on the other", f"{V['e1_lo_br_ba']} / {V['e1_br_lo_ba']}", "–", "–"]])


def t_sample():
    import csv
    rows = list(csv.DictReader(open(RES / "hil_serial_test_windows.csv")))
    pick = [r for r in rows if r["y"] == "0"][::60][:3] + [r for r in rows if r["y"] == "1"][::300][:3]
    lab = {"0": "no leak", "1": "leak"}
    return md_table(["Window", "True label", "Nano float32", "Nano int8", "Features (ms)", "int8 MLP (ms)"],
                    [[r["idx"], lab[r["y"]], lab[r["pred_f32"]], lab[r["pred_i8"]], f"{int(r['feat_us']) / 1000:.2f}",
                      f"{int(r['mlp_i8_us']) / 1000:.2f}"] for r in pick])


def t_resources():
    n = V["nano"]
    return md_table(["Resource (deployed int8 system)", "Used", "Available / limit"], [
        ["Time per window: features", f"{n['ble0_opt1_feat_us_med']} ms", "–"],
        ["Time per window: int8 neural network", f"{n['ble0_opt1_mlp_i8_us_med']} ms", "–"],
        ["Time per window: total", f"{V['total_opt_ms']} ms", "256 ms (one window of audio)"],
        ["Flash (whole program incl. OS, Bluetooth, model)", f"{V['flash_final']} B", "983,040 B"],
        ["  of which the int8 model", f"{V['st_int8']} B", "–"],
        ["RAM (static)", f"{V['ram_final']} B", "262,144 B"],
        ["Stack used at run time", f"{V['stack_hwm']} B", f"{V['stack_size']} B"]])


def t_platforms_short():
    return md_table(["Platform", "Speed for this model", "Power", "Fits a sensor on a pipe?", "Measured here?"], [
        ["Arduino Nano 33 BLE (microcontroller)", f"{V['total_opt_ms']} ms per window", "milliwatts, battery", "yes", "yes"],
        ["Desktop CPU", f"{V['host_i8_us']} µs (inference only)", "tens of watts", "no", "yes (reference)"],
        ["Raspberry Pi class computer", "far more than needed", "watts", "marginal", "no"],
        ["NVIDIA Jetson Nano (GPU)", "far more than needed", "several watts", "no", "no"],
        ["Google Coral (Edge TPU)", "far more than needed; needs a host", "watts", "no", "no"],
        ["FPGA (Xilinx PYNQ / Kria)", "far more than needed; long design time", "watts", "possible, but costly", "no"],
        ["Cloud (send the audio)", "fast, but needs a live link", "radio dominates", "no", "no"]])


def t_opt_short():
    return md_table(["Technique (course)", "Implementation", "Effect (measured)"], [
        ["Data reduction", "Resampled 8 kHz → 4 kHz", f"no accuracy change ({V['e4_fs4000_lo_ba']} vs {V['e4_fs8000_lo_ba']})"],
        ["Feature extraction", "1024 audio samples → 64 band features", "small, fixed-size network input"],
        ["Quantisation (INT8)", "Post-training int8 with TensorFlow Lite", f"model {V['st_fp32_all']} → {V['st_int8']} B, same accuracy ({V['dev_ba_f32']} vs {V['dev_ba_i8']})"],
        ["Pruning (tested on desktop only)", "Removed the smallest weights, no retraining",
         f"accuracy {V['prune_0_ba']} → {V['prune_50_ba']} at 50 % removed, {V['prune_90_ba']} at 90 %"],
        ["Fast kernels", "Used the Cortex-M4 SIMD instruction for int8 and a float-only log function",
         f"int8 network {V['nano']['ble0_opt1_mlp_i8_us_med']} ms, features {V['nano']['ble0_opt1_feat_us_med']} ms per window"]])


def t_cm():
    m = V["dev_cm_i8"]
    return md_table(["", "Predicted: no leak", "Predicted: leak"], [
        ["Actual: no leak", f"{m[0][0]} (true negative)", f"{m[0][1]} (false alarm)"],
        ["Actual: leak", f"{m[1][0]} (missed leak)", f"{m[1][1]} (true positive)"]])


TABLES = {"complexity_short": t_complexity_short, "tests": t_tests, "sample": t_sample, "resources": t_resources, "platforms_short": t_platforms_short,
          "opt_short": t_opt_short, "cm": t_cm,
          "protocols_short": t_protocols_short, "latency_short": t_latency_short, "memory_short": t_memory_short,
          "ble_short": t_ble_short, "metrics_short": t_metrics_short, "int8_short": t_int8_short, "co_short": t_co_short,
          "problem": t_problem, "complexity": t_complexity, "platforms": t_platforms, "compression": t_compression,
          "metrics": t_metrics, "co": t_co,
          "dataset": t_dataset, "protocols": t_protocols, "per_condition": t_per_condition, "e4": t_e4,
          "e5": t_e5, "latency": t_latency, "memory": t_memory, "ble": t_ble}


# ---------------- code excerpts and captured output ----------------
def extract_function(path, signature):
    src = (ROOT / path).read_text().splitlines()
    start = next(i for i, l in enumerate(src) if l.startswith(signature))
    end = next(i for i in range(start, len(src)) if src[i] == "}")
    return "\n".join(src[start:end + 1])


def extract_py(path, name):
    """A top-level Python function, verbatim, from 'def name' up to the next top-level line."""
    src = (ROOT / path).read_text().splitlines()
    start = next(i for i, l in enumerate(src) if l.startswith(f"def {name}("))
    end = start + 1
    while end < len(src) and (not src[end] or src[end][0] in " \t"):
        end += 1
    while not src[end - 1].strip():
        end -= 1
    return "\n".join(src[start:end])


def extract_between(path, begin, end):
    t = (ROOT / path).read_text()
    i = t.index(begin)
    j = t.index(end, i)
    return t[i:j].rstrip()


CODE = {
    "py_features": ("python/features.py: features of one batch of windows (the Nano runs the same steps in C++)",
                    lambda: extract_py("python/features.py", "window_features")),
    "py_model": ("python/train.py: the Keras model", lambda: extract_py("python/train.py", "build_model")),
    "py_convert": ("python/export.py: TensorFlow Lite conversion, float32 and int8",
                   lambda: extract_py("python/export.py", "convert")),
    "c_dense_i8": ("firmware/leak_nano/mlp.cpp: one int8 dense layer on the Nano",
                   lambda: extract_function("firmware/leak_nano/mlp.cpp", "static void dense_i8(")),
    "plain": ("firmware/leak_nano/mlp.cpp: dense layers in plain C",
              lambda: extract_function("firmware/leak_nano/mlp.cpp", "static void dense_f32(")
              + "\n\n" + extract_function("firmware/leak_nano/mlp.cpp", "static void dense_i8(")
              + "\n\n" + extract_function("firmware/leak_nano/mlp.cpp", "void mlp_quantize_input(")
              + "\n\n" + extract_function("firmware/leak_nano/mlp.cpp", "void mlp_i8(")),
    "mlp_i8": ("firmware/leak_nano/mlp.cpp: int8 FULLY_CONNECTED chain (TFLite reference arithmetic)",
               lambda: extract_function("firmware/leak_nano/mlp.cpp", "static int32_t sat_rounding_doubling_high_mul")
               + "\n\n" + extract_function("firmware/leak_nano/mlp.cpp", "static int32_t rounding_divide_by_pot")
               + "\n\n" + extract_function("firmware/leak_nano/mlp.cpp", "__attribute__((optimize(\"O2\"))) static int32_t dot_q7_simd")
               + "\n\n" + extract_function("firmware/leak_nano/mlp.cpp", "static void dense_i8_fast(")),
    "fastpath": ("firmware/leak_nano/dsp.cpp: magnitude + log stage (optimized and baseline paths)",
                 lambda: extract_between("firmware/leak_nano/dsp.cpp", "  // Packed output:", "  uint32_t t3 = micros();")),
}


def output_block(name):
    if name == "selftest":
        st = json.loads((RES / "hil_serial_test.json").read_text())
        return ("Self-test reply captured from the Nano (command 'S', results/hil_serial_test.json)",
                json.dumps(st["selftest"], indent=1))
    if name == "build":
        lines = [l for l in (RES / "build_output.txt").read_text().splitlines()
                 if l.startswith(("Sketch uses", "Global variables")) or re.match(r"^\S+\s+\d+\.\d+\.\d+\s+/", l)
                 or "Used library" in l or "Used platform" in l]
        return ("arduino-cli compile output of the final firmware (results/build_output.txt)", "\n".join(lines))
    raise KeyError(name)


# ---------------- fill the template ----------------
def fill(text):
    def sub(m):
        key = m.group(1)
        if key.startswith("table:"):
            name, _, cap = key[6:].partition("|")
            return (f"<!-- tcap: {cap.strip()} -->\n" if cap.strip() else "") + TABLES[name]()
        if key.startswith("code:"):
            cap, fn = CODE[key[5:]]
            return f"```code {cap}\n{fn()}\n```"
        if key.startswith("output:"):
            cap, body = output_block(key[7:])
            return f"```output {cap}\n{body}\n```"
        if key not in V:
            raise SystemExit(f"missing measured value for placeholder {{{{{key}}}}}")
        return str(V[key])
    return re.sub(r"\{\{([^}]+)\}\}", sub, text)


# ---------------- markdown subset -> flowables ----------------
SYMBOL_CHARS = "→≈Σ≤≥−"


def inline(t):
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    t = re.sub(r"&lt;(/?)(sub|super)&gt;", r"<\1\2>", t)
    t = re.sub(r"`([^`]+)`", lambda m: f'<font name="{MONO}" size="{CODE_PT}">{m.group(1)}</font>', t)
    # markdown links [text](url) -> clickable blue link
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<link href="\2" color="blue">\1</link>', t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\w*])\*([^*]+)\*(?![\w*])", r"<i>\1</i>", t)
    t = re.sub(f"([{SYMBOL_CHARS}])", r'<font name="Symbol">\1</font>', t)
    return t


def rule():
    return HRFlowable(width="100%", thickness=0.5, color=colors.black, spaceBefore=1, spaceAfter=8)


class Heading(Paragraph):
    def __init__(self, text, level):
        super().__init__(inline(text), S["h1" if level == 1 else "h2"])
        self.toc_level, self.toc_text = level - 1, text


def make_table(lines):
    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines if not re.match(r"^\|?\s*-+", l.replace("|", "").strip() or "-")]
    rows = [r for r in rows if not all(re.fullmatch(r"-+", c) for c in r)]
    ncol = len(rows[0])
    data = [[Paragraph(inline(c), S["cellb"] if i == 0 else S["cell"]) for c in r] for i, r in enumerate(rows)]
    # column widths: never narrower than the longest word in the column; spread the rest by text length
    from reportlab.pdfbase.pdfmetrics import stringWidth
    def plain(c):
        return re.sub(r"[`*]", "", c)
    minw = [max(stringWidth(w, "Helvetica-Bold", 8.3) for r in rows for w in (plain(r[j]).split() or [""])) + 12
            for j in range(ncol)]
    want = [min(max(stringWidth(plain(r[j]), "Helvetica", 8.3) for r in rows) + 12, FRAME_W * (0.45 if ncol <= 4 else 0.3)) for j in range(ncol)]
    if sum(want) <= FRAME_W:
        widths = [w * FRAME_W / sum(want) for w in want]
    else:
        extra = FRAME_W - sum(minw)
        slack = [max(w - m, 0) for w, m in zip(want, minw)]
        widths = [m + (extra * sl / sum(slack) if sum(slack) and extra > 0 else 0) for m, sl in zip(minw, slack)]
        if extra < 0:
            widths = [w * FRAME_W / sum(minw) for w in minw]
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                           ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]))
    return t


CODE_COLS = int((FRAME_W - 12) / (0.602 * CODE_PT))


def wrap_code(body):
    out = []
    for line in body.splitlines():
        while len(line) > CODE_COLS:
            cut = line.rfind(" ", 0, CODE_COLS)
            cut = cut if cut > CODE_COLS // 2 else CODE_COLS
            out.append(line[:cut])
            line = "      " + line[cut:].lstrip()
        out.append(line)
    return "\n".join(out)


def code_block(kind_caption, body):
    kind, _, cap = kind_caption.partition(" ")
    body = wrap_code(body)
    pre = XPreformatted(body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), S["code"])
    box = Table([[pre]], colWidths=[FRAME_W], hAlign="LEFT")
    box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.black), ("LEFTPADDING", (0, 0), (-1, -1), 5),
                             ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    label = ("Listing: " if kind == "code" else "Captured output: ") + cap
    return [CondPageBreak(60 * mm), KeepTogether([Paragraph(inline(label), S["caption"]), box]), Spacer(1, 6)]


def figure(path, caption, label=""):
    p = ROOT / path
    from PIL import Image as PILImage
    with PILImage.open(p) as im:
        w, h = im.size
    width = FRAME_W
    height = width * h / w
    if height > 95 * mm:
        height = 95 * mm
        width = height * w / h
    cap = (f"<b>{label}:</b> " if label else "") + inline(caption)
    return [KeepTogether([Image(str(p), width=width, height=height), Paragraph(cap, S["capc"])])]


def md_to_flowables(md):
    lines = md.splitlines()
    out, para, i = [], [], 0
    num = dict(sec=0, fig=0, tab=0, tcap=None)  # figure / table numbering per section ("Fig. 3.2", "Table 6.1")

    def label(kind):
        num[kind] += 1
        n = f"{num['sec']}.{num[kind]}" if num["sec"] else str(num[kind])
        return ("Fig. " if kind == "fig" else "Table ") + n

    def flush():
        if para:
            out.append(Paragraph(inline(" ".join(para)), S["body"]))
            para.clear()
    while i < len(lines):
        l = lines[i]
        if l.startswith("```"):
            flush()
            head = l[3:].strip()
            j = i + 1
            while not lines[j].startswith("```"):
                j += 1
            out += code_block(head, "\n".join(lines[i + 1:j]))
            i = j + 1
            continue
        if l.strip() in ("<!-- appendix -->", "<!-- pagebreak -->"):
            flush()
            out.append(PageBreak())
        elif l.startswith("<!-- tcap:"):
            flush()
            num["tcap"] = l[len("<!-- tcap:"):].rsplit("-->", 1)[0].strip()
        elif l.startswith("# "):
            flush()
            m = re.match(r"(\d+)\.", l[2:])
            if m:
                num.update(sec=int(m.group(1)), fig=0, tab=0)
            out += [CondPageBreak(45 * mm), Heading(l[2:], 1), rule()]
        elif l.startswith("## "):
            flush()
            out += [CondPageBreak(35 * mm), Heading(l[3:], 2),
                    HRFlowable(width="100%", thickness=0.3, color=colors.black, spaceBefore=0, spaceAfter=7)]
        elif l.startswith("!["):
            flush()
            m = re.match(r"!\[(.*)\]\((.*)\)", l)
            out += figure(m.group(2), m.group(1), label("fig"))
        elif l.startswith("|"):
            flush()
            j = i
            while j < len(lines) and lines[j].startswith("|"):
                j += 1
            tb = make_table(lines[i:j])
            if num["tcap"]:
                cap = Paragraph(f"<b>{label('tab')}:</b> " + inline(num["tcap"]), S["tcap"])
                num["tcap"] = None
                out += [KeepTogether([cap, tb]) if j - i <= 12 else cap, *([] if j - i <= 12 else [tb]), Spacer(1, 7)]
            else:
                out += [KeepTogether([tb]) if j - i <= 12 else tb, Spacer(1, 7)]
            i = j
            continue
        elif re.match(r"^\s*(- |\d+\. )", l):
            flush()
            m = re.match(r"^(\s*)(- |(\d+)\. )(.*)", l)
            indent = len(m.group(1))
            text = m.group(4)
            j = i + 1
            while j < len(lines) and lines[j].startswith(" " * (indent + 2)) and not re.match(r"^\s*(- |\d+\. )", lines[j]):
                text += " " + lines[j].strip()
                j += 1
            bullet = "•" if m.group(2) == "- " else f"{m.group(3)}."
            st = ParagraphStyle("b2", parent=S["bullet"], leftIndent=12 + indent * 6, bulletIndent=2 + indent * 6)
            out.append(Paragraph(inline(text), st, bulletText=bullet))
            i = j
            continue
        elif not l.strip():
            flush()
        else:
            para.append(l.strip())
        i += 1
    flush()
    # Bind each heading (and its rule) to the block that follows, so no heading is left alone at a page foot.
    bound, k = [], 0
    while k < len(out):
        f = out[k]
        if isinstance(f, Heading) and k + 2 < len(out) and isinstance(out[k + 1], HRFlowable):
            nxt = out[k + 2]
            inner = list(nxt._content) if isinstance(nxt, KeepTogether) else [nxt]
            bound.append(KeepTogether([f, out[k + 1], *inner]))
            k += 3
            continue
        bound.append(f)
        k += 1
    return bound


# ---------------- document ----------------
class Doc(BaseDocTemplate):
    def __init__(self, path, title, **kw):
        super().__init__(path, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN,
                         bottomMargin=MARGIN, title=title, author="", **kw)
        self.doc_title = title
        self.title = "IOT Assignment"  # browser-tab title only
        frame = Frame(MARGIN, MARGIN, FRAME_W, PAGE_H - 2 * MARGIN, id="f", leftPadding=0, rightPadding=0,
                      topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=self.footer)])

    def footer(self, canv, doc):
        canv.saveState()
        y = MARGIN - 9 * mm
        canv.setLineWidth(0.4)
        canv.line(MARGIN, y + 4 * mm, PAGE_W - MARGIN, y + 4 * mm)
        canv.setFont("Helvetica", 8)
        canv.drawString(MARGIN, y, self.doc_title)
        canv.drawRightString(PAGE_W - MARGIN, y, f"Page {doc.page}")
        canv.restoreState()

    def afterFlowable(self, f):
        if isinstance(f, Heading) and f.toc_level == 0:
            self.notify("TOCEntry", (f.toc_level, f.toc_text, self.page))


def cover(meta, how_to_run=True):
    fl = [Spacer(1, 6 * mm), Paragraph(inline(meta["title"]), S["title"]), Paragraph(inline(meta["subtitle"]), S["subtitle"])]
    who = ParagraphStyle("who", fontName="Helvetica-Oblique", fontSize=8.8, leading=11.5, alignment=2)
    for label, key in (("Name", "name"), ("Reg. No.", "regno"), ("Section", "section"), ("Date", "date")):
        v = meta.get(key, "").strip()
        if v and v not in ("–", "-"):
            fl.append(Paragraph(inline(f"{label}: {v}"), who))
    fl += [Spacer(1, 22 * mm), Paragraph("Aim", S["h2"]), rule(), Paragraph(inline(meta["aim"]), S["body"]),
           Spacer(1, 3 * mm), Paragraph("Contents", S["h2"]), rule()]
    toc = TableOfContents()
    toc.levelStyles = [S["toc1"], S["toc2"]]
    toc.dotsMinLevel = 0
    fl.append(toc)
    run = ("uv venv -p 3.11 .venv && uv pip install -p .venv/bin/python -r host/requirements.txt\n"
           ".venv/bin/python -I python/load.py              # dataset inventory\n"
           ".venv/bin/python -I python/train.py all         # E1-E4 on the host\n"
           ".venv/bin/python -I python/export.py            # TFLite + firmware headers\n"
           "firmware/leak_nano/build.sh                     # compile + upload (Nano on USB)\n"
           ".venv/bin/python -I host/serial_stream.py --subset test --tag test   # HIL, E5\n"
           ".venv/bin/python -I host/bench_nano.py          # E6, Nano side\n"
           ".venv/bin/python -I host/bench_tflite.py        # E6, host side\n"
           ".venv/bin/python -I host/ble_stream.py --burst 1 --realtime-s 30 --tag patched_rt  # E7\n"
           ".venv/bin/python -I report/make_figures.py && .venv/bin/python -I report/build_pdf.py")
    if not how_to_run:
        fl.append(PageBreak())
        return fl
    fl += [Spacer(1, 3 * mm), Paragraph("How to run", S["h2"]), rule(),
           Paragraph(inline("Full steps, including the dataset download and its checksum: `README.md`. In order:"), S["body"])]
    fl += code_block("code Reproduction commands", run)[1:]
    fl.append(PageBreak())
    return fl


def main():
    # --full builds the detailed version (report_template_full.md -> report_full.pdf/.md)
    full = "--full" in sys.argv
    stem = "report_full" if full else "report"
    tpl = (REP / ("report_template_full.md" if full else "report_template.md")).read_text()
    m = re.match(r"^---\n(.*?)\n---\n", tpl, re.S)
    meta = dict(re.findall(r"^(\w+): (.*)$", m.group(1), re.M))
    body = fill(tpl[m.end():])
    (REP / f"{stem}.md").write_text(f"# {meta['title']}\n\n*{meta['subtitle']}*\n\n**Aim.** {meta['aim']}\n\n"
                                   + body.replace("](figures/", "](../figures/"))
    doc = Doc(str(REP / f"{stem}.pdf"), meta["title"])
    doc.author = meta.get("name", "")
    doc.multiBuild(cover(meta, how_to_run=False) + md_to_flowables(body))
    print(f"wrote report/{stem}.md and report/{stem}.pdf")
    if "--pages" in sys.argv:
        import fitz
        out = REP / "_pages"
        out.mkdir(exist_ok=True)
        for f in out.glob("*.png"):
            f.unlink()
        d = fitz.open(REP / f"{stem}.pdf")
        for k, page in enumerate(d):
            page.get_pixmap(dpi=60).save(out / f"page-{k + 1:02d}.png")
        print(f"{len(d)} pages rendered to report/_pages/")


if __name__ == "__main__":
    main()

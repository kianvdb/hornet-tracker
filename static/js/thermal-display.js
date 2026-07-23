/**
 * THERMAL DISPLAY — render MLX90640 frames op canvas
 *
 * Backend stuurt elke ~150ms een 'thermal_frame' event met:
 *   { data: [768 floats °C],
 *     min, max, avg, fps,
 *     baseline: null | [768 floats °C] }
 *
 * Render-modus:
 *   - 'normal': hybride scaling (auto-scale per frame > MIN_RANGE, anders
 *               vaste range gecentreerd op gemiddelde)
 *   - 'detection': verschil-rendering tov baseline. Pixels op/onder
 *                  baseline = zwart, pixels boven baseline kleuren warm
 *                  naar mate ze afwijken
 *
 * Palette: operator kan tussen vier paletten kiezen via dropdown:
 *   - iron      klassieke thermal cam look (paars → rood → geel → wit)
 *   - inferno   wetenschappelijk standaard (zwart → paars → rood → geel)
 *   - grayscale geprint/thesis-rapport vriendelijk (zwart → wit)
 *   - rainbow   blauw → groen → geel → rood (oude FLIR-stijl)
 */

const THERMAL_WIDTH = 32;
const THERMAL_HEIGHT = 24;
const MIN_RANGE = 15.0;

let renderMode = 'normal';
let activePalette = 'iron';
let baselineAvailable = false;
const paletteCache = {};


function buildIronPalette() {
    const palette = new Uint8Array(256 * 3);
    for (let i = 0; i < 256; i++) {
        let r, g, b;

        if (i < 110) {
            const t = i / 110;
            r = Math.round(t * 50); g = 0; b = Math.round(t * 80);
        } else if (i < 170) {
            const t = (i - 110) / 60;
            r = 50 + Math.round(t * 150); g = 0; b = 80 - Math.round(t * 80);
        } else if (i < 210) {
            const t = (i - 170) / 40;
            r = 200 + Math.round(t * 55); g = Math.round(t * 80); b = 0;
        } else if (i < 240) {
            const t = (i - 210) / 30;
            r = 255; g = 80 + Math.round(t * 140); b = 0;
        } else {
            const t = (i - 240) / 15;
            r = 255; g = 220 + Math.round(t * 35); b = Math.round(t * 220);
        }

        palette[i * 3]     = Math.min(255, r);
        palette[i * 3 + 1] = Math.min(255, g);
        palette[i * 3 + 2] = Math.min(255, b);
    }
    return palette;
}


function buildInfernoPalette() {
    return buildPaletteFromKeypoints([
        [0, 0, 0, 0],
        [64, 40, 11, 84],
        [128, 120, 28, 109],
        [192, 220, 92, 50],
        [255, 252, 255, 164]
    ]);
}


function buildGrayscalePalette() {
    const palette = new Uint8Array(256 * 3);
    for (let i = 0; i < 256; i++) {
        palette[i * 3]     = i;
        palette[i * 3 + 1] = i;
        palette[i * 3 + 2] = i;
    }
    return palette;
}


function buildRainbowPalette() {
    return buildPaletteFromKeypoints([
        [0,     0,   0, 128],
        [64,    0, 128, 255],
        [128,   0, 255,   0],
        [192, 255, 255,   0],
        [255, 255,   0,   0]
    ]);
}


function buildPaletteFromKeypoints(keypoints) {
    const palette = new Uint8Array(256 * 3);
    for (let i = 0; i < 256; i++) {
        let lo = keypoints[0], hi = keypoints[keypoints.length - 1];
        for (let k = 0; k < keypoints.length - 1; k++) {
            if (i >= keypoints[k][0] && i <= keypoints[k + 1][0]) {
                lo = keypoints[k];
                hi = keypoints[k + 1];
                break;
            }
        }
        const t = (i - lo[0]) / Math.max(1, hi[0] - lo[0]);
        palette[i * 3]     = Math.round(lo[1] + t * (hi[1] - lo[1]));
        palette[i * 3 + 1] = Math.round(lo[2] + t * (hi[2] - lo[2]));
        palette[i * 3 + 2] = Math.round(lo[3] + t * (hi[3] - lo[3]));
    }
    return palette;
}


function getActivePalette() {
    if (paletteCache[activePalette]) return paletteCache[activePalette];

    let palette;
    switch (activePalette) {
        case 'inferno':   palette = buildInfernoPalette(); break;
        case 'grayscale': palette = buildGrayscalePalette(); break;
        case 'rainbow':   palette = buildRainbowPalette(); break;
        case 'iron':
        default:          palette = buildIronPalette(); break;
    }
    paletteCache[activePalette] = palette;
    return palette;
}


function renderNormalMode(imgData, data, frameMin, frameMax) {
    const palette = getActivePalette();
    const rawRange = frameMax - frameMin;
    let scaleMin, scaleMax;
    if (rawRange < MIN_RANGE) {
        const center = (frameMin + frameMax) / 2;
        scaleMin = center - MIN_RANGE / 2;
        scaleMax = center + MIN_RANGE / 2;
    } else {
        scaleMin = frameMin;
        scaleMax = frameMax;
    }
    const range = scaleMax - scaleMin;

    if (range < 0.01) {
        for (let i = 0; i < THERMAL_WIDTH * THERMAL_HEIGHT; i++) {
            const px = i * 4;
            imgData.data[px] = 0;
            imgData.data[px + 1] = 0;
            imgData.data[px + 2] = 0;
            imgData.data[px + 3] = 255;
        }
        return;
    }

    for (let i = 0; i < data.length; i++) {
        let idx = Math.round(((data[i] - scaleMin) / range) * 255);
        if (idx < 0) idx = 0;
        if (idx > 255) idx = 255;

        const palOffset = idx * 3;
        const px = i * 4;
        imgData.data[px]     = palette[palOffset];
        imgData.data[px + 1] = palette[palOffset + 1];
        imgData.data[px + 2] = palette[palOffset + 2];
        imgData.data[px + 3] = 255;
    }
}


function renderDetectionMode(imgData, data, baseline) {
    const palette = getActivePalette();
    const DETECTION_RANGE = 10.0;

    for (let i = 0; i < data.length; i++) {
        const diff = data[i] - baseline[i];
        const px = i * 4;

        if (diff <= 0) {
            imgData.data[px] = 0;
            imgData.data[px + 1] = 0;
            imgData.data[px + 2] = 0;
        } else {
            let idx = Math.round((diff / DETECTION_RANGE) * 255);
            if (idx > 255) idx = 255;
            const palOffset = idx * 3;
            imgData.data[px]     = palette[palOffset];
            imgData.data[px + 1] = palette[palOffset + 1];
            imgData.data[px + 2] = palette[palOffset + 2];
        }
        imgData.data[px + 3] = 255;
    }
}


function renderThermalFrame(payload) {
    const canvas = document.getElementById('thermal-canvas');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const offCanvas = document.createElement('canvas');
    offCanvas.width = THERMAL_WIDTH;
    offCanvas.height = THERMAL_HEIGHT;
    const offCtx = offCanvas.getContext('2d');
    const imgData = offCtx.createImageData(THERMAL_WIDTH, THERMAL_HEIGHT);

    if (renderMode === 'detection' && payload.baseline) {
        renderDetectionMode(imgData, payload.data, payload.baseline);
    } else {
        renderNormalMode(imgData, payload.data, payload.min, payload.max);
    }

    offCtx.putImageData(imgData, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(offCanvas, 0, 0, canvas.width, canvas.height);

    const noSignal = document.getElementById('thermal-no-signal');
    if (noSignal) noSignal.style.display = 'none';
}


function updateThermalStats(min, max, avg, fps) {
    const minEl = document.getElementById('thermal-min-val');
    const avgEl = document.getElementById('thermal-avg-val');
    const maxEl = document.getElementById('thermal-max-val');
    const fpsEl = document.getElementById('thermal-fps-val');

    if (minEl) minEl.textContent = min.toFixed(1) + '°';
    if (avgEl) avgEl.textContent = avg.toFixed(1) + '°';
    if (maxEl) maxEl.textContent = max.toFixed(1) + '°';
    if (fpsEl) fpsEl.textContent = fps.toFixed(1);
}


function updateThermalButtons() {
    const toggleBtn = document.getElementById('thermal-mode-toggle-btn');
    const clearBtn = document.getElementById('thermal-baseline-clear-btn');
    const modeLabel = document.getElementById('thermal-mode-label');

    if (!toggleBtn || !clearBtn) return;

    if (baselineAvailable) {
        toggleBtn.disabled = false;
        clearBtn.disabled = false;
    } else {
        toggleBtn.disabled = true;
        clearBtn.disabled = true;
        renderMode = 'normal';
    }

    if (renderMode === 'detection') {
        toggleBtn.classList.add('active');
        if (modeLabel) modeLabel.textContent = 'Detectie';
    } else {
        toggleBtn.classList.remove('active');
        if (modeLabel) modeLabel.textContent = 'Normaal';
    }
}


function handleThermalFrame(payload) {
    if (!payload || !payload.data) return;

    const hadBaseline = baselineAvailable;
    baselineAvailable = payload.baseline !== null && payload.baseline !== undefined;
    if (hadBaseline !== baselineAvailable) {
        updateThermalButtons();
    }

    // Alleen renderen wanneer het thermisch paneel open staat. De backend
    // blijft frames sturen (thermal_loop kent de UI-staat niet); we slaan
    // hier het dure werk over — off-screen canvas, ImageData, 768 pixels.
    // De baseline-state hierboven blijft wel bijgewerkt, zodat de knoppen
    // meteen kloppen zodra de operator naar de tab wisselt.
    if (typeof window.getActiveTab === 'function' &&
        window.getActiveTab() !== 'thermal') {
        return;
    }

    renderThermalFrame(payload);
    updateThermalStats(payload.min, payload.max, payload.avg, payload.fps);
}


function handleThermalBaselineResult(result) {
    if (!result.success && window.showToast) {
        window.showToast(result.message || 'Baseline actie mislukt', 'error');
    }
}


function setThermalBaseline() {
    if (!window.socket) return;
    window.socket.emit('thermal_baseline_set');
}


function clearThermalBaseline() {
    if (!window.socket) return;
    window.socket.emit('thermal_baseline_clear');
    renderMode = 'normal';
    baselineAvailable = false;
    updateThermalButtons();
}


function toggleThermalMode() {
    if (!baselineAvailable) return;
    renderMode = (renderMode === 'normal') ? 'detection' : 'normal';
    updateThermalButtons();
}


function setThermalPalette(key) {
    const valid = ['iron', 'inferno', 'grayscale', 'rainbow'];
    activePalette = valid.includes(key) ? key : 'iron';
}


/**
 * Open/sluit het palette-menu. Event stoppen zodat de click-outside
 * handler (op document) hem niet meteen weer dichtklikt.
 */
function toggleThermalPaletteMenu(event) {
    event.stopPropagation();
    const dropdown = document.getElementById('thermal-palette-dropdown');
    if (!dropdown) return;
    dropdown.classList.toggle('open');
}


/**
 * Operator selecteert een palette uit de lijst. Sluit menu, update
 * trigger-label, update actieve state in lijst, en notify render-logica.
 */
function selectThermalPalette(key, displayLabel) {
    setThermalPalette(key);

    const dropdown = document.getElementById('thermal-palette-dropdown');
    const label = document.getElementById('thermal-palette-trigger-label');

    if (label) label.textContent = displayLabel;
    if (dropdown) dropdown.classList.remove('open');

    // Active state op de juiste option zetten
    document.querySelectorAll('.thermal-palette-option').forEach((el) => {
        if (el.dataset.value === key) {
            el.classList.add('active');
        } else {
            el.classList.remove('active');
        }
    });
}


// Click buiten dropdown sluit hem. Eén globale listener volstaat;
// dropdown.open class toggles in/uit zonder dat de listener weet
// of de dropdown bestaat.
document.addEventListener('click', (event) => {
    const dropdown = document.getElementById('thermal-palette-dropdown');
    if (!dropdown) return;
    if (!dropdown.contains(event.target)) {
        dropdown.classList.remove('open');
    }
});

// Window exports
window.renderThermalFrame = renderThermalFrame;
window.updateThermalStats = updateThermalStats;
window.handleThermalFrame = handleThermalFrame;
window.handleThermalBaselineResult = handleThermalBaselineResult;
window.setThermalBaseline = setThermalBaseline;
window.clearThermalBaseline = clearThermalBaseline;
window.toggleThermalMode = toggleThermalMode;
window.setThermalPalette = setThermalPalette;
window.toggleThermalPaletteMenu = toggleThermalPaletteMenu;
window.selectThermalPalette = selectThermalPalette;
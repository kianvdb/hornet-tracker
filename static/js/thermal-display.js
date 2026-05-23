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
 * Sensor is 24 hoog × 32 breed. Pixel index i = row * 32 + col.
 */

const THERMAL_WIDTH = 32;
const THERMAL_HEIGHT = 24;
const MIN_RANGE = 15.0;

// Render-modus state. Switcht via toggleThermalMode().
let renderMode = 'normal';

// Of we een baseline hebben ontvangen van de backend.
let baselineAvailable = false;

let ironPalette = null;

function buildIronPalette() {
    const palette = new Uint8Array(256 * 3);
    for (let i = 0; i < 256; i++) {
        let r, g, b;

        if (i < 110) {
            const t = i / 110;
            r = Math.round(t * 50);
            g = 0;
            b = Math.round(t * 80);
        } else if (i < 170) {
            const t = (i - 110) / 60;
            r = 50 + Math.round(t * 150);
            g = 0;
            b = 80 - Math.round(t * 80);
        } else if (i < 210) {
            const t = (i - 170) / 40;
            r = 200 + Math.round(t * 55);
            g = Math.round(t * 80);
            b = 0;
        } else if (i < 240) {
            const t = (i - 210) / 30;
            r = 255;
            g = 80 + Math.round(t * 140);
            b = 0;
        } else {
            const t = (i - 240) / 15;
            r = 255;
            g = 220 + Math.round(t * 35);
            b = Math.round(t * 220);
        }

        palette[i * 3]     = Math.min(255, r);
        palette[i * 3 + 1] = Math.min(255, g);
        palette[i * 3 + 2] = Math.min(255, b);
    }
    return palette;
}


function renderNormalMode(imgData, data, frameMin, frameMax) {
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
        imgData.data[px]     = ironPalette[palOffset];
        imgData.data[px + 1] = ironPalette[palOffset + 1];
        imgData.data[px + 2] = ironPalette[palOffset + 2];
        imgData.data[px + 3] = 255;
    }
}


/**
 * Detectie-modus: verschil tov baseline. Pixels onder baseline -> zwart.
 * Boven baseline mapt 0-10°C verschil op de volledige palette.
 */
function renderDetectionMode(imgData, data, baseline) {
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
            imgData.data[px]     = ironPalette[palOffset];
            imgData.data[px + 1] = ironPalette[palOffset + 1];
            imgData.data[px + 2] = ironPalette[palOffset + 2];
        }
        imgData.data[px + 3] = 255;
    }
}


function renderThermalFrame(payload) {
    const canvas = document.getElementById('thermal-canvas');
    if (!canvas) return;

    if (!ironPalette) ironPalette = buildIronPalette();

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


// Window exports
window.renderThermalFrame = renderThermalFrame;
window.updateThermalStats = updateThermalStats;
window.handleThermalFrame = handleThermalFrame;
window.handleThermalBaselineResult = handleThermalBaselineResult;
window.setThermalBaseline = setThermalBaseline;
window.clearThermalBaseline = clearThermalBaseline;
window.toggleThermalMode = toggleThermalMode;
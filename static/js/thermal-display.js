/**
 * THERMAL DISPLAY — render MLX90640 frames op canvas
 *
 * Backend stuurt elke ~150ms een 'thermal_frame' event met:
 *   { data: [768 floats °C],
 *     min, max, avg, fps }
 *
 * Scaling strategie — hybride:
 *   - Bij range >= MIN_RANGE: auto-scale per frame (max contrast)
 *   - Bij range < MIN_RANGE: vaste range gecentreerd op gemiddelde
 *     (voorkomt dat ruis in uniforme scènes als heatmap wordt weergegeven)
 *
 * Sensor is 24 hoog × 32 breed. Pixel index i = row * 32 + col.
 */

const THERMAL_WIDTH = 32;
const THERMAL_HEIGHT = 24;

// Minimum temperatuur-spreiding (°C) waarbij we auto-scaling toelaten.
const MIN_RANGE = 15.0;

let ironPalette = null;

function buildIronPalette() {
    const palette = new Uint8Array(256 * 3);
    for (let i = 0; i < 256; i++) {
        let r, g, b;

        // Midden-temperaturen moeten donker blijven. Pas vanaf 65% palette
        // gaan we naar warm-tinten — geeft uniforme scènes een rustig uiterlijk
        // en behoudt heat-spots scherp zichtbaar.
        if (i < 110) {
            // 0-110: zwart → donker paars (43% van het palette is "koel")
            const t = i / 110;
            r = Math.round(t * 50);
            g = 0;
            b = Math.round(t * 80);
        } else if (i < 170) {
            // 110-170: donker paars → donker rood
            const t = (i - 110) / 60;
            r = 50 + Math.round(t * 150);
            g = 0;
            b = 80 - Math.round(t * 80);
        } else if (i < 210) {
            // 170-210: donker rood → rood
            const t = (i - 170) / 40;
            r = 200 + Math.round(t * 55);
            g = Math.round(t * 80);
            b = 0;
        } else if (i < 240) {
            // 210-240: rood → oranje
            const t = (i - 210) / 30;
            r = 255;
            g = 80 + Math.round(t * 140);
            b = 0;
        } else {
            // 240-255: oranje → geel → wit (alleen voor échte hot-spots)
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


function renderThermalFrame(data, frameMin, frameMax) {
    const canvas = document.getElementById('thermal-canvas');
    if (!canvas) return;

    if (!ironPalette) {
        ironPalette = buildIronPalette();
    }

    const ctx = canvas.getContext('2d');

    const offCanvas = document.createElement('canvas');
    offCanvas.width = THERMAL_WIDTH;
    offCanvas.height = THERMAL_HEIGHT;
    const offCtx = offCanvas.getContext('2d');
    const imgData = offCtx.createImageData(THERMAL_WIDTH, THERMAL_HEIGHT);

    // Hybride scale-range bepaling
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
            imgData.data[px]     = 0;
            imgData.data[px + 1] = 0;
            imgData.data[px + 2] = 0;
            imgData.data[px + 3] = 255;
        }
    } else {
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


function handleThermalFrame(payload) {
    if (!payload || !payload.data) return;
    renderThermalFrame(payload.data, payload.min, payload.max);
    updateThermalStats(payload.min, payload.max, payload.avg, payload.fps);
}


// Window exports
window.renderThermalFrame = renderThermalFrame;
window.updateThermalStats = updateThermalStats;
window.handleThermalFrame = handleThermalFrame;
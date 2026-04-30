// ============================================================
// Tab switching (URL / File input)
// ============================================================
document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
        document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
        tab.classList.add("active");
        document.getElementById(`tab-${tab.dataset.tab}`).classList.add("active");
    });
});

// ============================================================
// File upload - drag & drop and click
// ============================================================
const uploadArea = document.getElementById("upload-area");
const fileInput = document.getElementById("file-input");
const fileNameDisplay = document.getElementById("file-name");

uploadArea.addEventListener("click", () => fileInput.click());

uploadArea.addEventListener("dragover", (e) => {
    e.preventDefault();
    uploadArea.classList.add("dragover");
});

uploadArea.addEventListener("dragleave", () => {
    uploadArea.classList.remove("dragover");
});

uploadArea.addEventListener("drop", (e) => {
    e.preventDefault();
    uploadArea.classList.remove("dragover");
    if (e.dataTransfer.files.length > 0) {
        fileInput.files = e.dataTransfer.files;
        showFileName(e.dataTransfer.files[0]);
    }
});

fileInput.addEventListener("change", () => {
    if (fileInput.files.length > 0) {
        showFileName(fileInput.files[0]);
    }
});

function showFileName(file) {
    fileNameDisplay.textContent = file.name;
}

// ============================================================
// Panel tab switching (AI总结 / 转写原文)
// ============================================================
document.getElementById("summary-tabs").addEventListener("click", (e) => {
    const tab = e.target.closest(".panel-tab");
    if (!tab) return;

    const panel = tab.dataset.panel;
    const panelEl = tab.closest(".summary-panel");

    // Switch active tab
    panelEl.querySelectorAll(".panel-tab").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");

    // Switch active view
    panelEl.querySelectorAll(".panel-view").forEach(v => v.classList.remove("active"));
    panelEl.querySelector(`#panel-${panel}`).classList.add("active");

    // Toggle export buttons based on active tab
    const exportSummaryBtn = document.getElementById("export-summary");
    const exportTranscriptionBtn = document.getElementById("export-transcription");
    if (panel === "summary") {
        exportSummaryBtn.style.display = "";
        exportTranscriptionBtn.style.display = "none";
    } else {
        exportSummaryBtn.style.display = "none";
        exportTranscriptionBtn.style.display = "";
    }
});

// ============================================================
// UI helpers
// ============================================================
function setLoading(btn, loading) {
    const textEl = btn.querySelector(".btn-text");
    const loadEl = btn.querySelector(".btn-loading");
    if (loading) {
        textEl.style.display = "none";
        loadEl.style.display = "inline-flex";
        btn.disabled = true;
    } else {
        textEl.style.display = "inline";
        loadEl.style.display = "none";
        btn.disabled = false;
    }
}

function showLoading(text) {
    document.getElementById("loading").style.display = "block";
    document.getElementById("loading-text").textContent = text || "正在处理，请稍候...";
    document.getElementById("error").style.display = "none";
    document.getElementById("results").style.display = "none";
}

function hideLoading() {
    document.getElementById("loading").style.display = "none";
}

function showError(message) {
    document.getElementById("error").style.display = "flex";
    document.getElementById("error-text").textContent = message;
    hideLoading();
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

// Store the last API response data
let lastResultData = null;

function showResults(data) {
    lastResultData = data;
    hideLoading();
    document.getElementById("error").style.display = "none";

    // Metadata
    const meta = data.metadata;
    document.getElementById("result-meta").innerHTML = `
        <div class="meta-item">
            <span class="meta-label">标题</span>
            <span class="meta-value">${escapeHtml(meta.title)}</span>
        </div>
        <div class="meta-item">
            <span class="meta-label">时长</span>
            <span class="meta-value">${escapeHtml(meta.duration)}</span>
        </div>
        <div class="meta-item">
            <span class="meta-label">来源</span>
            <span class="meta-value">${escapeHtml(meta.source)}</span>
        </div>
    `;

    // Transcription — grouped by 2-minute intervals, clickable timestamps
    const transEl = document.getElementById("transcription-result");
    const grouped = data.transcription.grouped;
    if (grouped && grouped.length > 0) {
        transEl.innerHTML = grouped
            .map((g) => `
                <div class="time-group">
                    <div class="time-group-header">
                        <span class="timestamp" data-seek-ms="${g.start_ms}" title="点击跳转到视频">${g.start} - ${g.end}</span>
                        <span class="time-group-count">${g.sentence_count} 句</span>
                    </div>
                    <div class="time-group-text">${escapeHtml(g.text)}</div>
                </div>
            `)
            .join("");
        const totalCount = grouped.reduce((sum, g) => sum + g.sentence_count, 0);
        document.getElementById("sentence-count").textContent = `${totalCount} 条 · ${grouped.length} 段`;

        // Attach click-to-seek on timestamps
        transEl.querySelectorAll(".timestamp[data-seek-ms]").forEach((el) => {
            el.addEventListener("click", () => {
                const ms = parseInt(el.dataset.seekMs, 10);
                const player = document.getElementById("video-player");
                if (player && player.src && player.src !== window.location.href) {
                    seekVideo(ms);
                } else if (lastResultData && lastResultData.metadata && lastResultData.metadata.source) {
                    // No embedded video - open source URL in new tab
                    window.open(lastResultData.metadata.source, "_blank");
                }
            });
        });
    } else {
        transEl.textContent = "无转写结果";
        document.getElementById("sentence-count").textContent = "";
    }

    // Reset to AI总结 tab
    const summaryPanel = document.querySelector(".summary-panel");
    summaryPanel.querySelectorAll(".panel-tab").forEach(t => t.classList.remove("active"));
    summaryPanel.querySelector('.panel-tab[data-panel="summary"]').classList.add("active");
    summaryPanel.querySelectorAll(".panel-view").forEach(v => v.classList.remove("active"));
    summaryPanel.querySelector("#panel-summary").classList.add("active");

    // Summary
    const summaryEl = document.getElementById("summary-result");
    summaryEl.innerHTML = renderMarkdown(data.summary || "未生成总结");

    // Video player
    setupVideoPlayer(data.video_url);

    // Clear deepen
    document.getElementById("deepen-results").innerHTML = "";
    document.getElementById("deepen-input").value = "";

    // Show results first so container has dimensions, then render mind map
    document.getElementById("results").style.display = "block";

    requestAnimationFrame(() => {
        setTimeout(() => renderMindMap(), 150);
    });

    // Scroll to results
    setTimeout(() => {
        document.getElementById("results").scrollIntoView({ behavior: "smooth", block: "start" });
    }, 100);
}

// ============================================================
// Video player
// ============================================================
function setupVideoPlayer(videoUrl) {
    const section = document.getElementById("video-section");
    const player = document.getElementById("video-player");

    if (videoUrl) {
        player.src = videoUrl;
        section.style.display = "block";
    } else {
        player.src = "";
        section.style.display = "none";
    }
}

function seekVideo(ms) {
    const player = document.getElementById("video-player");
    if (!player || !player.src || player.src === window.location.href) return;

    const seconds = ms / 1000;

    // Scroll video into view immediately
    const section = document.getElementById("video-section");
    if (section) {
        section.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    // If video metadata is ready, seek immediately
    if (player.readyState >= 1) {
        player.currentTime = seconds;
        player.play().catch(() => {});
    } else {
        // Wait for metadata to load, then seek
        const onReady = () => {
            player.currentTime = seconds;
            player.play().catch(() => {});
            player.removeEventListener("loadedmetadata", onReady);
        };
        player.addEventListener("loadedmetadata", onReady);
        // Also try canplay as a fallback
        player.addEventListener("canplay", function onCanPlay() {
            player.currentTime = seconds;
            player.play().catch(() => {});
            player.removeEventListener("canplay", onCanPlay);
        }, { once: true });
    }
}

// ============================================================
// Markdown rendering
// ============================================================
function renderMarkdown(text) {
    if (!text) return "<p>无内容</p>";

    let html = escapeHtml(text);

    // Code blocks
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, "<pre><code>$2</code></pre>");

    // Headings
    html = html.replace(/^#### (.+)$/gm, "<h4>$1</h4>");
    html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
    html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
    html = html.replace(/^# (.+)$/gm, "<h1>$1</h1>");

    // Bold and italic
    html = html.replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>");
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");

    // Inline code
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");

    // Horizontal rules
    html = html.replace(/^---$/gm, "<hr>");

    // Blockquote
    html = html.replace(/^> (.+)$/gm, "<blockquote>$1</blockquote>");

    // Unordered lists
    html = html.replace(/^[\s]*[-*] (.+)$/gm, "<li>$1</li>");
    html = html.replace(/(<li>.*<\/li>\n?)+/g, "<ul>$&</ul>");

    // Ordered lists
    html = html.replace(/^[\s]*\d+\. (.+)$/gm, "<oli>$1</oli>");
    html = html.replace(/(<oli>.*<\/oli>\n?)+/g, "<ol>$&</ol>");
    html = html.replace(/<\/oli>/g, "</li>");
    html = html.replace(/<oli>/g, "<li>");

    // Paragraphs
    const lines = html.split("\n");
    let result = [];
    let inParagraph = false;

    for (let line of lines) {
        const isBlock = /^<(\/)?(h[1-4]|ul|ol|li|blockquote|pre|hr)/.test(line.trim());

        if (isBlock) {
            if (inParagraph) {
                result.push("</p>");
                inParagraph = false;
            }
            result.push(line);
        } else if (line.trim() === "") {
            if (inParagraph) {
                result.push("</p>");
                inParagraph = false;
            }
        } else {
            if (!inParagraph) {
                result.push("<p>");
                inParagraph = true;
            }
            result.push(line);
        }
    }
    if (inParagraph) result.push("</p>");

    return result.join("\n");
}

// ============================================================
// Mind map rendering via markmap-autoloader
// ============================================================
function renderMindMap() {
    if (!lastResultData) return;

    const summary = lastResultData.summary || "";
    if (!summary || summary.startsWith("Claude 总结失败")) {
        showMindmapPlaceholder("无总结内容");
        return;
    }

    const meta = lastResultData.metadata;
    const title = meta.title || "内容总结";
    const mindMapMarkdown = `# ${title}\n\n${summary}`;

    const wrap = document.getElementById("mindmap-wrap");
    const oldContainer = document.getElementById("mindmap-container");

    // Remove the old container entirely — autoloader tracks processed elements,
    // so we must replace the DOM node to force re-render on subsequent calls.
    const newContainer = document.createElement("div");
    newContainer.className = "markmap";
    newContainer.id = "mindmap-container";
    newContainer.innerHTML = `<script type="text/template">${mindMapMarkdown}<\/script>`;

    if (oldContainer) {
        oldContainer.replaceWith(newContainer);
    } else {
        wrap.appendChild(newContainer);
    }

    // Wait for markmap autoloader to be ready, then render
    waitForMarkmap(() => {
        try {
            markmap.autoLoader.renderAll();

            // Auto-fit after render
            setTimeout(() => {
                const svg = newContainer.querySelector("svg");
                if (svg) {
                    svg.style.width = "100%";
                    svg.style.height = "100%";
                }
            }, 500);
        } catch (e) {
            console.warn("Mind map render error:", e);
            showMindmapPlaceholder("思维导图渲染失败");
        }
    });
}

function waitForMarkmap(callback, retries = 20) {
    if (typeof markmap !== "undefined" && markmap.autoLoader) {
        callback();
    } else if (retries > 0) {
        setTimeout(() => waitForMarkmap(callback, retries - 1), 250);
    } else {
        showMindmapPlaceholder("思维导图库加载失败，请刷新页面");
    }
}

function showMindmapPlaceholder(msg) {
    const container = document.getElementById("mindmap-container");
    if (container) {
        container.innerHTML = `
            <div style="display:flex;align-items:center;justify-content:center;height:100%;color:#9ca3af;font-size:14px;font-family:Inter,sans-serif;">
                ${escapeHtml(msg)}
            </div>
        `;
    }
}

// Mind map controls
document.getElementById("mindmap-fit").addEventListener("click", () => {
    renderMindMap();
});

document.getElementById("mindmap-zoom-in").addEventListener("click", () => {
    const svg = document.querySelector("#mindmap-container svg");
    if (svg) {
        const current = parseFloat(svg.style.transform?.match(/scale\(([^)]+)\)/)?.[1] || 1);
        svg.style.transform = `scale(${current * 1.3})`;
        svg.style.transformOrigin = "center center";
    }
});

document.getElementById("mindmap-zoom-out").addEventListener("click", () => {
    const svg = document.querySelector("#mindmap-container svg");
    if (svg) {
        const current = parseFloat(svg.style.transform?.match(/scale\(([^)]+)\)/)?.[1] || 1);
        svg.style.transform = `scale(${current / 1.3})`;
        svg.style.transformOrigin = "center center";
    }
});

// ============================================================
// Deepen - follow-up questions
// ============================================================
document.getElementById("deepen-submit").addEventListener("click", async () => {
    const input = document.getElementById("deepen-input");
    const question = input.value.trim();
    if (!question) return;
    if (!lastResultData || !lastResultData.transcription) return;

    const btn = document.getElementById("deepen-submit");
    const loading = document.getElementById("deepen-loading");
    const resultsEl = document.getElementById("deepen-results");

    btn.disabled = true;
    loading.style.display = "flex";

    try {
        const resp = await fetch("/api/deepen", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                question: question,
                full_text: lastResultData.transcription.full_text,
            }),
        });
        const data = await resp.json();

        if (data.success) {
            const div = document.createElement("div");
            div.className = "deepen-item";
            div.innerHTML = `
                <div class="deepen-q"><strong>Q:</strong> ${escapeHtml(question)}</div>
                <div class="markdown-body">${renderMarkdown(data.answer)}</div>
            `;
            resultsEl.prepend(div);
            input.value = "";
        } else {
            const div = document.createElement("div");
            div.className = "deepen-item";
            div.innerHTML = `<div style="color:#dc2626;">错误：${escapeHtml(data.error)}</div>`;
            resultsEl.prepend(div);
        }
    } catch (err) {
        const div = document.createElement("div");
        div.className = "deepen-item";
        div.innerHTML = `<div style="color:#dc2626;">请求失败：${escapeHtml(err.message)}</div>`;
        resultsEl.prepend(div);
    } finally {
        btn.disabled = false;
        loading.style.display = "none";
    }
});

document.getElementById("deepen-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
        document.getElementById("deepen-submit").click();
    }
});

// ============================================================
// URL form submit
// ============================================================
document.getElementById("url-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = document.getElementById("url-input").value.trim();
    if (!url) return;

    const btn = document.getElementById("url-submit");
    setLoading(btn, true);
    showLoading("正在下载音频并转写，这可能需要几分钟...");

    try {
        const resp = await fetch("/api/transcribe-url", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url }),
        });
        const data = await resp.json();

        if (data.success) {
            showLoading("转写完成，正在生成 AI 总结...");
            setTimeout(() => showResults(data), 300);
        } else {
            showError(data.error || "处理失败");
        }
    } catch (err) {
        showError("请求失败: " + err.message);
    } finally {
        setLoading(btn, false);
    }
});

// ============================================================
// File form submit
// ============================================================
document.getElementById("file-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const file = fileInput.files[0];

    if (!file) {
        showError("请选择文件");
        return;
    }

    const btn = document.getElementById("file-submit");
    setLoading(btn, true);
    showLoading("正在上传并转写，这可能需要几分钟...");

    const formData = new FormData();
    formData.append("file", file);

    try {
        const resp = await fetch("/api/transcribe-file", {
            method: "POST",
            body: formData,
        });
        const data = await resp.json();

        if (data.success) {
            showLoading("转写完成，正在生成 AI 总结...");
            setTimeout(() => showResults(data), 300);
        } else {
            showError(data.error || "处理失败");
        }
    } catch (err) {
        showError("请求失败: " + err.message);
    } finally {
        setLoading(btn, false);
    }
});

// ============================================================
// Settings
// ============================================================
document.getElementById("settings-btn").addEventListener("click", () => {
    document.getElementById("settings-modal").style.display = "flex";
    loadLLMConfig();
});

document.getElementById("settings-close").addEventListener("click", () => {
    document.getElementById("settings-modal").style.display = "none";
});

document.getElementById("settings-modal").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) {
        e.currentTarget.style.display = "none";
    }
});

// LLM backend toggle
document.querySelectorAll('input[name="llm-backend"]').forEach((radio) => {
    radio.addEventListener("change", () => {
        const apiSettings = document.getElementById("custom-api-settings");
        apiSettings.style.display = radio.value === "custom_api" && radio.checked ? "block" : "none";
    });
});

// API key show/hide
document.getElementById("toggle-api-key").addEventListener("click", () => {
    const input = document.getElementById("api-key");
    input.type = input.type === "password" ? "text" : "password";
});

// Load LLM config
async function loadLLMConfig() {
    try {
        const resp = await fetch("/api/config");
        const data = await resp.json();
        if (!data.success) return;

        const cfg = data.config;
        // Set backend radio
        const backendRadio = document.querySelector(`input[name="llm-backend"][value="${cfg.llm_backend}"]`);
        if (backendRadio) {
            backendRadio.checked = true;
            backendRadio.dispatchEvent(new Event("change"));
        }

        // Set custom API fields
        const api = cfg.custom_api || {};
        const formatRadio = document.querySelector(`input[name="api-format"][value="${api.format || "openai"}"]`);
        if (formatRadio) formatRadio.checked = true;
        document.getElementById("api-base-url").value = api.base_url || "";
        document.getElementById("api-model").value = api.model || "";
        document.getElementById("api-key").value = "";

        // Show key hint
        const hint = document.getElementById("api-key-hint");
        if (api.has_key) {
            hint.textContent = `当前已配置: ${api.api_key_masked}`;
        } else {
            hint.textContent = "";
        }
    } catch (e) {
        console.warn("Load config failed:", e);
    }
}

// Save LLM config
document.getElementById("save-llm-config").addEventListener("click", async () => {
    const status = document.getElementById("llm-config-status");
    const backend = document.querySelector('input[name="llm-backend"]:checked')?.value;
    const apiKey = document.getElementById("api-key").value.trim();

    const payload = {
        llm_backend: backend,
        custom_api: {
            format: document.querySelector('input[name="api-format"]:checked')?.value || "openai",
            base_url: document.getElementById("api-base-url").value.trim(),
            model: document.getElementById("api-model").value.trim(),
        },
    };
    // Only send api_key if user typed a new one
    if (apiKey) {
        payload.custom_api.api_key = apiKey;
    }

    try {
        const resp = await fetch("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (data.success) {
            status.textContent = "已保存";
            status.className = "setting-status success";
            // Clear key field and reload hint
            document.getElementById("api-key").value = "";
            loadLLMConfig();
        } else {
            status.textContent = data.error || "保存失败";
            status.className = "setting-status error";
        }
    } catch (e) {
        status.textContent = "请求失败";
        status.className = "setting-status error";
    }
    setTimeout(() => { status.textContent = ""; }, 3000);
});

// ============================================================
// Export functions
// ============================================================
function getExportSettings() {
    const format = document.querySelector('input[name="export-format"]:checked')?.value || "md";
    const prefix = document.getElementById("export-prefix")?.value || "EchoScribe";
    return { format, prefix };
}

function downloadFile(content, filename, mimeType) {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function exportSummary() {
    if (!lastResultData) return;
    const { format, prefix } = getExportSettings();
    const title = lastResultData.metadata?.title || "untitled";
    const safeTitle = title.replace(/[\\/:*?"<>|]/g, "_").substring(0, 50);
    const filename = `${prefix}_summary_${safeTitle}.${format}`;
    const content = lastResultData.summary || "无总结内容";
    const mime = format === "md" ? "text/markdown;charset=utf-8" : "text/plain;charset=utf-8";
    downloadFile(content, filename, mime);
}

function exportTranscription() {
    if (!lastResultData || !lastResultData.transcription) return;
    const { format, prefix } = getExportSettings();
    const title = lastResultData.metadata?.title || "untitled";
    const safeTitle = title.replace(/[\\/:*?"<>|]/g, "_").substring(0, 50);
    const filename = `${prefix}_transcription_${safeTitle}.${format}`;

    let content;
    if (format === "md") {
        const grouped = lastResultData.transcription.grouped || [];
        const lines = [`# ${title} - 转写原文\n`];
        for (const g of grouped) {
            lines.push(`## ${g.start} - ${g.end}\n`);
            lines.push(g.text + "\n");
        }
        content = lines.join("\n");
    } else {
        content = lastResultData.transcription.full_text || "无转写内容";
    }
    const mime = format === "md" ? "text/markdown;charset=utf-8" : "text/plain;charset=utf-8";
    downloadFile(content, filename, mime);
}

function exportMindMap() {
    const svg = document.querySelector("#mindmap-container svg");
    if (!svg) return;
    const { prefix } = getExportSettings();
    const title = lastResultData?.metadata?.title || "untitled";
    const safeTitle = title.replace(/[\\/:*?"<>|]/g, "_").substring(0, 50);
    const filename = `${prefix}_mindmap_${safeTitle}.svg`;

    const svgData = new XMLSerializer().serializeToString(svg);
    const svgWithNs = svgData.replace('<svg', '<svg xmlns="http://www.w3.org/2000/svg"');
    downloadFile(svgWithNs, filename, "image/svg+xml;charset=utf-8");
}

// Wire up export buttons
document.getElementById("export-summary").addEventListener("click", exportSummary);
document.getElementById("export-transcription").addEventListener("click", exportTranscription);
document.getElementById("export-mindmap").addEventListener("click", exportMindMap);

// ============================================================
// Resizable panels
// ============================================================
(function initResize() {
    const handle = document.getElementById("resize-handle");
    const dashboard = document.querySelector(".dashboard");
    if (!handle || !dashboard) return;

    // Restore saved ratio
    const savedRatio = localStorage.getItem("flycut-panel-ratio");
    if (savedRatio) {
        const ratio = parseFloat(savedRatio);
        if (ratio > 0.15 && ratio < 0.85) {
            dashboard.style.gridTemplateColumns = `${ratio}fr 6px ${1 - ratio}fr`;
        }
    }

    handle.addEventListener("mousedown", (e) => {
        e.preventDefault();
        const startX = e.clientX;
        // Capture the initial column widths in pixels at drag start
        const cols = getComputedStyle(dashboard).gridTemplateColumns.split(/\s+/).map(parseFloat);
        const startLeftPx = cols[0];
        const startRightPx = cols[2];
        const fixedWidth = startLeftPx + startRightPx; // constant throughout drag

        handle.classList.add("active");
        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";

        const onMouseMove = (e) => {
            const delta = e.clientX - startX;
            let newLeftPx = startLeftPx + delta;
            // Clamp: at least 15% and at most 85% of available space
            const minPx = fixedWidth * 0.15;
            const maxPx = fixedWidth * 0.85;
            newLeftPx = Math.max(minPx, Math.min(maxPx, newLeftPx));
            const newRightPx = fixedWidth - newLeftPx;
            dashboard.style.gridTemplateColumns = `${newLeftPx}px 6px ${newRightPx}px`;
        };

        const onMouseUp = () => {
            handle.classList.remove("active");
            document.body.style.cursor = "";
            document.body.style.userSelect = "";
            document.removeEventListener("mousemove", onMouseMove);
            document.removeEventListener("mouseup", onMouseUp);

            // Save ratio
            const cols = getComputedStyle(dashboard).gridTemplateColumns.split(/\s+/).map(parseFloat);
            const ratio = cols[0] / (cols[0] + cols[2]);
            localStorage.setItem("flycut-panel-ratio", ratio.toString());
        };

        document.addEventListener("mousemove", onMouseMove);
        document.addEventListener("mouseup", onMouseUp);
    });
})();

// ============================================================
// Mind map fullscreen
// ============================================================
document.getElementById("mindmap-fullscreen").addEventListener("click", () => {
    const panel = document.querySelector(".mindmap-panel");
    const btn = document.getElementById("mindmap-fullscreen");
    if (!panel) return;

    if (!document.fullscreenElement) {
        panel.requestFullscreen().then(() => {
            panel.classList.add("fullscreen");
            btn.title = "退出全屏";
        }).catch(() => {});
    } else {
        document.exitFullscreen().then(() => {
            panel.classList.remove("fullscreen");
            btn.title = "全屏";
        }).catch(() => {});
    }
});

document.addEventListener("fullscreenchange", () => {
    const panel = document.querySelector(".mindmap-panel");
    const btn = document.getElementById("mindmap-fullscreen");
    if (!document.fullscreenElement && panel) {
        panel.classList.remove("fullscreen");
        if (btn) btn.title = "全屏";
    }
});

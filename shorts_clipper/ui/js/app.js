
        // Progressive Disclosure Advanced Settings Toggle
        function toggleAdvancedSettings() {
            const container = document.getElementById('advanced-settings-container');
            const btn = document.getElementById('adv-settings-toggle-btn');
            if (!container || !btn) return;
            if (container.style.display === 'none') {
                container.style.display = 'block';
                btn.textContent = 'Hide';
            } else {
                container.style.display = 'none';
                btn.textContent = 'Show';
            }
        }

        // Unified Toast Notification System
        function showToast(message, type = 'info', duration = 4000) {
            const container = document.getElementById('toast-container');
            if (!container) return;
            
            const toast = document.createElement('div');
            toast.className = `toast toast-${type}`;
            
            let iconClass = 'fa-circle-info';
            if (type === 'success') iconClass = 'fa-circle-check';
            else if (type === 'error') iconClass = 'fa-circle-exclamation';
            else if (type === 'warning') iconClass = 'fa-triangle-exclamation';
            
            toast.innerHTML = `
                <div class="toast-icon"><i class="fa-solid ${iconClass}"></i></div>
                <div class="toast-content">${message}</div>
                <button class="toast-close" onclick="this.parentElement.classList.add('hide'); setTimeout(() => this.parentElement.remove(), 300);">&times;</button>
            `;
            
            container.appendChild(toast);
            
            if (duration > 0) {
                setTimeout(() => {
                    if (toast.parentElement) {
                        toast.classList.add('hide');
                        setTimeout(() => {
                            if (toast.parentElement) {
                                toast.remove();
                            }
                        }, 300);
                    }
                }, duration);
            }
        }

        // Terminal collapsing toggle
        function toggleTerminalCollapsed(forcedState) {
            const wrapper = document.getElementById('terminal-container-wrapper');
            const toggleIcon = document.getElementById('terminal-toggle-icon');
            const toggleText = document.getElementById('terminal-toggle-text');
            if (!wrapper || !toggleIcon || !toggleText) return;

            let isCollapsed = wrapper.style.display === 'none';
            if (forcedState !== undefined) {
                isCollapsed = !forcedState;
            }

            if (isCollapsed) {
                wrapper.style.display = 'block';
                toggleIcon.classList.remove('fa-chevron-down');
                toggleIcon.classList.add('fa-chevron-up');
                toggleText.textContent = 'Hide Details';
                localStorage.setItem('terminal_collapsed', 'false');
                const feed = document.getElementById('terminal-body-feed');
                if (feed) feed.scrollTop = feed.scrollHeight;
            } else {
                wrapper.style.display = 'none';
                toggleIcon.classList.remove('fa-chevron-up');
                toggleIcon.classList.add('fa-chevron-down');
                toggleText.textContent = 'Show Details';
                localStorage.setItem('terminal_collapsed', 'true');
            }
        }

        // Central activity message transition
        function setActivityMessage(msg) {
            const el = document.getElementById('activity-status-message');
            if (!el) return;
            if (el.textContent === msg) return;
            
            el.style.opacity = '0';
            el.style.transform = 'translateY(-4px)';
            setTimeout(() => {
                el.textContent = msg;
                if (msg === "Ready." || msg === "Done.") {
                    el.style.color = "var(--ghost)";
                } else if (msg === "Task failed." || msg === "Task cancelled.") {
                    el.style.color = "var(--ash)";
                } else {
                    el.style.color = "var(--cyan)";
                }
                el.style.opacity = '1';
                el.style.transform = 'translateY(0)';
            }, 150);
        }

        // Mobile sidebar helper
        function toggleMobileSidebar(state) {
            if (state) {
                document.body.classList.add('sidebar-mobile-open');
            } else {
                document.body.classList.remove('sidebar-mobile-open');
            }
        }

        let currentUrlTranscript = "";
        let currentSegments = [];

        // Upload progress tracking
        let uploadStartTime = null;
        let lastUploadPct = 0;

        function handleUploadProgress(logLine) {
            const container = document.getElementById('active-upload-progress-container');
            if (!container) return;
            const stats = document.getElementById('active-upload-stats');
            const bar = document.getElementById('active-upload-progress-bar');
            
            const logLower = logLine.toLowerCase();
            
            const isUploadStart = (logLower.includes('uploading') || logLower.includes('publishing')) || 
                                  logLower.includes('initializing upload') || 
                                  logLower.includes('uploading clip');
                                  
            if (isUploadStart && !logLower.includes('publishing completed') && !logLower.includes('successfully published')) {
                container.style.display = 'flex';
                if (!uploadStartTime) {
                    uploadStartTime = Date.now();
                }
                lastUploadPct = 0;
                bar.style.width = '0%';
                stats.textContent = '0% | Speed: Calculating... | ETA: Calculating...';
                return;
            }
            
            const match = logLine.match(/Uploaded\s+(\d+)%/i);
            if (match) {
                const pct = parseInt(match[1], 10);
                container.style.display = 'flex';
                bar.style.width = pct + '%';
                
                if (!uploadStartTime) {
                    uploadStartTime = Date.now();
                }
                
                const elapsed = (Date.now() - uploadStartTime) / 1000;
                let speedText = "Calculating...";
                let etaText = "Calculating...";
                
                if (elapsed > 0.5 && pct > 0) {
                    const estSizeMB = 10.0; // typical short video size
                    const uploadedMB = (pct / 100) * estSizeMB;
                    const speed = uploadedMB / elapsed; // MB/s
                    
                    if (speed < 1) {
                        speedText = Math.round(speed * 1024) + " KB/s";
                    } else {
                        speedText = speed.toFixed(1) + " MB/s";
                    }
                    
                    const remainingSeconds = (estSizeMB - uploadedMB) / speed;
                    if (remainingSeconds <= 0.1) {
                        etaText = "Finishing...";
                    } else if (remainingSeconds < 60) {
                        etaText = Math.round(remainingSeconds) + "s left";
                    } else {
                        const mins = Math.floor(remainingSeconds / 60);
                        const secs = Math.round(remainingSeconds % 60);
                        etaText = `${mins}m ${secs}s left`;
                    }
                }
                
                stats.textContent = `${pct}% | Speed: ${speedText} | ETA: ${etaText}`;
                return;
            }
            
            const isDone = logLower.includes('uploaded to youtube successfully') || 
                           logLower.includes('upload complete') || 
                           logLower.includes('uploaded manual clip successfully') ||
                           logLower.includes('successfully published') ||
                           logLower.includes('uploaded successfully to requested platforms') ||
                           logLower.includes('job finished successfully') ||
                           logLower.includes('publishing completed');
                           
            if (isDone) {
                container.style.display = 'flex';
                bar.style.width = '100%';
                stats.textContent = '100% | Upload Complete!';
                setTimeout(() => {
                    container.style.display = 'none';
                    uploadStartTime = null;
                }, 4000);
                return;
            }
            
            const isFailed = logLower.includes('failed to upload') || 
                             logLower.includes('publishing failed') || 
                             logLower.includes('upload interrupted');
                             
            if (isFailed) {
                stats.textContent = 'Upload Failed.';
                bar.style.backgroundColor = 'var(--ash)';
                setTimeout(() => {
                    container.style.display = 'none';
                    uploadStartTime = null;
                }, 4000);
            }
            
            const isError = logLower.includes('failed') || 
                            logLower.includes('cancelled') ||
                            logLower.includes('error') ||
                            logLower.includes('error');
                            
            if (isError && (logLower.includes('upload') || logLower.includes('publish') || logLower.includes('youtube'))) {
                container.style.display = 'none';
                uploadStartTime = null;
            }
        }

        // App page navigation router
        function switchView(viewId) {
            toggleMobileSidebar(false);
            document.querySelectorAll('.app-view').forEach(view => {
                view.classList.remove('active');
            });
            document.querySelectorAll('.nav-item').forEach(item => {
                item.classList.remove('active');
            });
            document.getElementById(viewId).classList.add('active');
            
            // Highlight the correct menu item
            if (viewId === 'view-autopilot') {
                document.getElementById('nav-item-autopilot').classList.add('active');
            } else if (viewId === 'view-clipper') {
                document.getElementById('nav-item-clipper').classList.add('active');
            } else if (viewId === 'view-library') {
                document.getElementById('nav-item-library').classList.add('active');
            } else if (viewId === 'view-watchdog') {
                document.getElementById('nav-item-watchdog').classList.add('active');
                fetchWatchdog();
            } else if (viewId === 'view-settings') {
                document.getElementById('nav-item-settings').classList.add('active');
            }
            
            // Highlight/unhighlight mini settings button
            const miniGear = document.getElementById('mini-settings-btn-el');
            if (miniGear) {
                if (viewId === 'view-settings') {
                    miniGear.style.color = 'var(--gold2)';
                } else {
                    miniGear.style.color = '';
                }
            }
            
            if (viewId === 'view-library') {
                refreshLibrary();
            }
        }

        // Toggle sidebar collapse state
        function toggleSidebar() {
            const body = document.body;
            const icon = document.getElementById('toggle-icon');
            body.classList.toggle('sidebar-collapsed');
            if (body.classList.contains('sidebar-collapsed')) {
                icon.className = 'fa-solid fa-angle-right';
                localStorage.setItem('sidebar-collapsed', 'true');
            } else {
                icon.className = 'fa-solid fa-angle-left';
                localStorage.setItem('sidebar-collapsed', 'false');
            }
        }

        // Initialize logging stream via Server-Sent Events (SSE)
        function initLogStream() {
            const stream = new EventSource('/api/logs/stream');
            const term = document.getElementById('terminal-body-feed');
            const badge = document.getElementById('term-status-badge');
            
            stream.onmessage = function(event) {
                const text = event.data.trim();
                if (text) {
                    const lines = text.split('\n');
                    lines.forEach(line => {
                        const row = document.createElement('div');
                        row.className = 'log-line';
                        row.textContent = line;
                        
                        // Parse simple colors based on warning/error tags
                        if (line.includes('ERROR') || line.includes('❌')) {
                            row.style.color = '#ef4444';
                        } else if (line.includes('WARNING') || line.includes('⚠️')) {
                            row.style.color = '#fbbf24';
                        } else if (line.includes('SUCCESS') || line.includes('✅') || line.includes('READY')) {
                            row.style.color = '#34d399';
                        } else if (line.includes('🤖') || line.includes('🚀')) {
                            row.style.color = '#f472b6';
                        }
                        
                        term.appendChild(row);
                        
                        // Prevent infinite DOM growth and memory leak UI lag
                        if (term.childElementCount > 400) {
                            term.removeChild(term.firstChild);
                        }
                        
                        // Track upload statistics in real-time
                        handleUploadProgress(line);
                        
                        // Dynamically update progress status pipeline bar badges based on logs
                        if (activeJobPollInterval !== null) {
                            updatePipelineProgress(line);
                        }
                    });
                    term.scrollTop = term.scrollHeight;
                }
            };
            
            stream.onerror = function() {
                badge.textContent = "Offline, Reconnecting...";
                badge.style.color = "#ef4444";
                badge.style.background = "rgba(239,68,68,0.15)";
            };
            
            stream.onopen = function() {
                badge.textContent = "Stream Listening";
                badge.style.color = "var(--accent-green)";
                badge.style.background = "rgba(16,185,129,0.15)";
            };
        }

        let currentProgressPct = 0;
        let isLocalWhisperActive = false;

        function resetPipelineProgress() {
            currentProgressPct = 0;
            isLocalWhisperActive = false;
            
            const uploadContainer = document.getElementById('active-upload-progress-container');
            if (uploadContainer) {
                uploadContainer.style.display = 'none';
                uploadStartTime = null;
            }
            
            document.querySelectorAll('.stage-step').forEach(step => {
                step.className = 'stage-step';
            });
            document.querySelectorAll('.pipeline-bar').forEach(bar => {
                bar.className = 'pipeline-bar';
            });
            document.getElementById('step-idle').classList.add('active');
            
            // Also hide active upload container and reset start time
            const container = document.getElementById('active-upload-progress-container');
            if (container) {
                container.style.display = 'none';
            }
            uploadStartTime = null;
            setActivityMessage("Ready.");
        }

        // Set the active stage of the pipeline based on percentage
        function setPipelineProgress(pct) {
            if (pct > currentProgressPct) {
                currentProgressPct = pct;
            }

            const stepIdle = document.getElementById('step-idle');
            const stepScouting = document.getElementById('step-scouting');
            const stepDownloading = document.getElementById('step-downloading');
            const stepTranscribing = document.getElementById('step-transcribing');
            const stepRendering = document.getElementById('step-rendering');
            const stepPublishing = document.getElementById('step-publishing');
            
            const barScouting = document.getElementById('bar-scouting');
            const barDownloading = document.getElementById('bar-downloading');
            const barTranscribing = document.getElementById('bar-transcribing');
            const barRendering = document.getElementById('bar-rendering');
            const barPublishing = document.getElementById('bar-publishing');

            // Reset active states
            document.querySelectorAll('.stage-step').forEach(step => step.classList.remove('active'));
            document.querySelectorAll('.pipeline-bar').forEach(bar => bar.classList.remove('active'));

            if (currentProgressPct >= 100) {
                stepIdle.className = 'stage-step done';
                stepScouting.className = 'stage-step done';
                barScouting.className = 'pipeline-bar done';
                stepDownloading.className = 'stage-step done';
                barDownloading.className = 'pipeline-bar done';
                stepTranscribing.className = 'stage-step done';
                barTranscribing.className = 'pipeline-bar done';
                stepRendering.className = 'stage-step done';
                barRendering.className = 'pipeline-bar done';
                stepPublishing.className = 'stage-step done';
                barPublishing.className = 'pipeline-bar done';
                setActivityMessage("Done.");
            } else if (currentProgressPct >= 95) {
                stepIdle.className = 'stage-step done';
                stepScouting.className = 'stage-step done';
                barScouting.className = 'pipeline-bar done';
                stepDownloading.className = 'stage-step done';
                barDownloading.className = 'pipeline-bar done';
                stepTranscribing.className = 'stage-step done';
                barTranscribing.className = 'pipeline-bar done';
                stepRendering.className = 'stage-step done';
                barRendering.className = 'pipeline-bar done';
                barPublishing.className = 'pipeline-bar active';
                stepPublishing.className = 'stage-step active';
                setActivityMessage("Preparing export...");
            } else if (currentProgressPct >= 70) {
                stepIdle.className = 'stage-step done';
                stepScouting.className = 'stage-step done';
                barScouting.className = 'pipeline-bar done';
                stepDownloading.className = 'stage-step done';
                barDownloading.className = 'pipeline-bar done';
                stepTranscribing.className = 'stage-step done';
                barTranscribing.className = 'pipeline-bar done';
                barRendering.className = 'pipeline-bar active';
                stepRendering.className = 'stage-step active';
                setActivityMessage("Crafting your clip...");
            } else if (currentProgressPct >= 50) {
                stepIdle.className = 'stage-step done';
                stepScouting.className = 'stage-step done';
                barScouting.className = 'pipeline-bar done';
                stepDownloading.className = 'stage-step done';
                barDownloading.className = 'pipeline-bar done';
                barTranscribing.className = 'pipeline-bar active';
                stepTranscribing.className = 'stage-step active';
                if (isLocalWhisperActive) {
                    setActivityMessage("Continuing locally...");
                } else {
                    setActivityMessage("Listening carefully...");
                }
            } else if (currentProgressPct >= 30) {
                stepIdle.className = 'stage-step done';
                stepScouting.className = 'stage-step done';
                barScouting.className = 'pipeline-bar done';
                barDownloading.className = 'pipeline-bar active';
                stepDownloading.className = 'stage-step active';
                setActivityMessage("Preparing source material...");
            } else if (currentProgressPct >= 5) {
                stepIdle.className = 'stage-step done';
                barScouting.className = 'pipeline-bar active';
                stepScouting.className = 'stage-step active';
                setActivityMessage("Discovering opportunities...");
            } else {
                stepIdle.className = 'stage-step active';
                setActivityMessage("Ready.");
            }
        }

        // Parse logs in real-time to update the pipeline status visually
        function updatePipelineProgress(logLine) {
            if (logLine.includes('Falling back to local Whisper') || logLine.includes('with Whisper') || logLine.includes('Transcribing audio sample')) {
                isLocalWhisperActive = true;
            }

            if (logLine.includes('AUTOPILOT MODE: Scouting') || logLine.includes('🚀 ADVANCED VIRAL SCOUT')) {
                setPipelineProgress(5);
                setActivityMessage("Discovering opportunities...");
            } else if (logLine.includes('ranking') || logLine.includes('score') || logLine.includes('leaderboard')) {
                setActivityMessage("Comparing candidates...");
            } else if (logLine.includes('filter') || logLine.includes('skip') || logLine.includes('rejection')) {
                setActivityMessage("Finding standout moments...");
            } else if (logLine.includes('⬇ Downloading') || logLine.includes('DOWNLOADING') || logLine.includes('rough_audio.m4a')) {
                setPipelineProgress(30);
                setActivityMessage("Preparing source material...");
            } else if (logLine.includes('Running Whisper') || logLine.includes('🎙 Transcribing') || logLine.includes('Transcribing audio sample')) {
                setPipelineProgress(50);
                if (isLocalWhisperActive) {
                    setActivityMessage("Local transcription active...");
                } else {
                    setActivityMessage("Listening carefully...");
                }
            } else if (logLine.includes('VERTICAL CROP') || logLine.includes('Cropping to vertical')) {
                setPipelineProgress(70);
                setActivityMessage("Crafting your clip...");
            } else if (logLine.includes('BURNING SUBTITLES') || logLine.includes('Burning subtitles')) {
                setPipelineProgress(70);
                setActivityMessage("Building subtitles...");
            } else if (logLine.includes('Uploading') || logLine.includes('Publishing') || logLine.includes('Uploading Clip')) {
                setPipelineProgress(95);
                setActivityMessage("Preparing export...");
            } else if (logLine.includes('SUCCESS —') || logLine.includes('READY') || logLine.includes('Job finished successfully') || logLine.includes('uploaded to YouTube successfully') || logLine.includes('uploaded successfully to requested platforms') || logLine.includes('✅ Custom rendered clip ready')) {
                setPipelineProgress(100);
                setActivityMessage("Done.");
                
                // Immediately refresh library to show the new clip
                refreshLibrary();
                
                setTimeout(() => {
                    resetPipelineProgress();
                }, 12000);
            } else if (logLine.includes('Failed to upload') || logLine.includes('Pipeline failed') || logLine.includes('publishing failed')) {
                setActivityMessage("Task failed.");
                setTimeout(() => {
                    resetPipelineProgress();
                }, 12000);
            }
        }

        // Fetch server system config values and settings
        async function fetchSettings() {
            try {
                const res = await fetch('/api/settings');
                const data = await res.json();
                
                document.getElementById('set-gemini-key').value = data.gemini_api_key || "";
                document.getElementById('set-whisper-model').value = data.whisper_model;
                document.getElementById('set-whisper-device').value = data.whisper_device;
                document.getElementById('set-whisper-compute').value = data.whisper_compute_type;
                document.getElementById('set-video-codec').value = data.video_codec;
                document.getElementById('set-video-preset').value = data.video_preset;
                document.getElementById('set-age-days').value = data.scout_max_age_days;
                document.getElementById('set-enable-gpu').checked = data.enable_gpu;
                
                let subStyle = data.subtitle_style || "default";
                if (subStyle.startsWith("custom_")) {
                    document.getElementById('set-subtitle-style').value = "custom";
                    document.getElementById('custom-style-panel').style.display = 'grid';
                    
                    const parts = subStyle.split("_");
                    if (parts.length >= 7) {
                        document.getElementById('cust-font-name').value = parts[1] || "Inter Bold";
                        document.getElementById('cust-font-size').value = parts[2] || "58";
                        
                        const pri = "#" + (parts[3] || "F2F2F2");
                        const out = "#" + (parts[4] || "000000");
                        document.getElementById('cust-pri-color').value = pri;
                        document.getElementById('cust-pri-color-picker').value = pri;
                        document.getElementById('cust-out-color').value = out;
                        document.getElementById('cust-out-color-picker').value = out;
                        
                        document.getElementById('cust-out-val').value = parts[5] || "2.5";
                        document.getElementById('cust-shd-val').value = parts[6] || "1.0";
                    }
                } else {
                    document.getElementById('set-subtitle-style').value = subStyle;
                    document.getElementById('custom-style-panel').style.display = 'none';
                }
                
                // Update system statuses visually in navigation sidebar status box
                const elWhisper = document.getElementById('status-whisper');
                if (elWhisper) elWhisper.textContent = `${data.whisper_model.toUpperCase()} ${data.whisper_device.toUpperCase()}`;
                const elGpu = document.getElementById('status-gpu');
                if (elGpu) elGpu.textContent = data.enable_gpu ? "Enabled (NVENC)" : "Disabled";
                document.getElementById('stat-whisper-device').textContent = data.whisper_device.toUpperCase();
            } catch (err) {
                console.error("Failed to load settings from server:", err);
            }
        }

        async function saveSettings() {
            let subtitleStyle = document.getElementById('set-subtitle-style').value;
            if (subtitleStyle === 'custom') {
                const fontName = document.getElementById('cust-font-name').value.trim() || "Inter Bold";
                const fontSize = document.getElementById('cust-font-size').value.trim() || "58";
                const priHex = document.getElementById('cust-pri-color').value.trim().replace('#', '') || "F2F2F2";
                const outHex = document.getElementById('cust-out-color').value.trim().replace('#', '') || "000000";
                const outVal = document.getElementById('cust-out-val').value.trim() || "2.5";
                const shdVal = document.getElementById('cust-shd-val').value.trim() || "1.0";
                subtitleStyle = `custom_${fontName}_${fontSize}_${priHex}_${outHex}_${outVal}_${shdVal}`;
            }

            const body = {
                gemini_api_key: document.getElementById('set-gemini-key').value,
                whisper_model: document.getElementById('set-whisper-model').value,
                whisper_device: document.getElementById('set-whisper-device').value,
                whisper_compute_type: document.getElementById('set-whisper-compute').value,
                video_codec: document.getElementById('set-video-codec').value,
                video_preset: document.getElementById('set-video-preset').value,
                scout_max_age_days: parseInt(document.getElementById('set-age-days').value),
                enable_gpu: document.getElementById('set-enable-gpu').checked,
                subtitle_style: subtitleStyle
            };
            
            try {
                const res = await fetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                const data = await res.json();
                showToast(data.message, "success");
                fetchSettings();
            } catch (err) {
                showToast("Failed to save settings: " + err, "error");
            }
        }

        // Autopilot controls
        let activeJobPollInterval = null;
        let activeJobId = null;

        async function cancelCurrentJob() {
            if (!activeJobId) return;
            if (!confirm("Are you sure you want to cancel the active task? This will terminate all rendering processes immediately.")) return;
            
            const btn = document.getElementById('btn-cancel-job');
            const originalHTML = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin"></i> Cancelling...`;
            
            try {
                const res = await fetch(`/api/jobs/${activeJobId}/cancel`, {
                    method: 'POST'
                });
                if (!res.ok) {
                    const errData = await res.json();
                    throw new Error(errData.detail || "Cancellation failed");
                }
                const data = await res.json();
                console.log("Cancel request sent:", data);
                
                // Clear interval immediately and reset progress
                if (activeJobPollInterval) {
                    clearInterval(activeJobPollInterval);
                    activeJobPollInterval = null;
                }
                activeJobId = null;
                document.getElementById('active-job-cancel-container').style.display = 'none';
                resetPipelineProgress();
                showToast("Task cancellation triggered successfully.", "success");
            } catch (err) {
                showToast("Failed to cancel job: " + err.message, "error");
            } finally {
                btn.disabled = false;
                btn.innerHTML = originalHTML;
            }
        }
        
        function toggleCustomNicheInput(val) {
            const container = document.getElementById('custom-niche-container');
            if (val === 'custom_option') {
                container.style.display = 'block';
            } else {
                container.style.display = 'none';
                document.getElementById('auto-niche-custom').value = '';
            }
        }

        async function launchAutopilot() {
            let nicheVal = document.getElementById('auto-niche').value;
            if (nicheVal === 'custom_option') {
                nicheVal = document.getElementById('auto-niche-custom').value.trim() || null;
            }
            
            const body = {
                niche: nicheVal || null,
                keyword: document.getElementById('auto-keyword').value || null,
                channel: document.getElementById('auto-channel').value || null,
                count: parseInt(document.getElementById('auto-count').value),
                upload: document.getElementById('auto-upload').checked,
                privacy: document.getElementById('auto-privacy').value,
                scout_duration: document.getElementById('auto-duration').value
            };
            
            try {
                const res = await fetch('/api/autopilot', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                const data = await res.json();
                showToast(data.message, "success");
                if (data.job_id) {
                    trackJobProgress(data.job_id);
                }
            } catch (err) {
                showToast("Failed to launch autopilot: " + err, "error");
            }
        }

        function trackJobProgress(jobId) {
            if (activeJobPollInterval) clearInterval(activeJobPollInterval);
            
            activeJobId = jobId;
            document.getElementById('active-job-cancel-container').style.display = 'flex';
            document.getElementById('stat-autopilot-count').textContent = "Running";
            document.getElementById('stat-autopilot-count').style.color = "var(--cyan)";
            
            // Clear previous progress and set starting phase immediately
            resetPipelineProgress();
            setPipelineProgress(5);
            
            activeJobPollInterval = setInterval(async () => {
                try {
                    const res = await fetch('/api/jobs/' + jobId);
                    if (!res.ok) return;
                    const job = await res.json();
                    
                    if (job.status === 'running') {
                        setPipelineProgress(job.progress || 5);
                    } else if (job.status === 'done') {
                        clearInterval(activeJobPollInterval);
                        activeJobPollInterval = null;
                        activeJobId = null;
                        document.getElementById('active-job-cancel-container').style.display = 'none';
                        document.getElementById('stat-autopilot-count').textContent = "Ready";
                        document.getElementById('stat-autopilot-count').style.color = "";
                        
                        const uploadContainer = document.getElementById('active-upload-progress-container');
                        if (uploadContainer) uploadContainer.style.display = 'none';
                        uploadStartTime = null;
                        
                        setPipelineProgress(100);
                        refreshLibrary();
                        
                        setTimeout(() => {
                            resetPipelineProgress();
                        }, 12000);
                    } else if (job.status === 'failed') {
                        clearInterval(activeJobPollInterval);
                        activeJobPollInterval = null;
                        activeJobId = null;
                        document.getElementById('active-job-cancel-container').style.display = 'none';
                        document.getElementById('stat-autopilot-count').textContent = "Ready";
                        document.getElementById('stat-autopilot-count').style.color = "";
                        
                        showToast("Clipper Job failed: " + (job.error || "Unknown error"), "error");
                        setActivityMessage("Task failed.");
                        setTimeout(() => resetPipelineProgress(), 5000);
                    } else if (job.status === 'cancelled') {
                        clearInterval(activeJobPollInterval);
                        activeJobPollInterval = null;
                        activeJobId = null;
                        document.getElementById('active-job-cancel-container').style.display = 'none';
                        document.getElementById('stat-autopilot-count').textContent = "Ready";
                        document.getElementById('stat-autopilot-count').style.color = "";
                        
                        showToast("Clipper Job was cancelled.", "warning");
                        setActivityMessage("Task cancelled.");
                        setTimeout(() => resetPipelineProgress(), 5000);
                    }
                } catch (e) {
                    console.error("Failed to poll job status:", e);
                }
            }, 1500);
        }

        async function checkYouTubeStatus() {
            const box = document.getElementById('sidebar-yt-connection');
            try {
                const res = await fetch('/api/youtube/status');
                const data = await res.json();
                
                if (data.connected) {
                    box.innerHTML = `
                        <div style="display:flex; align-items:center; gap: 0.75rem; width: 100%;" title="${data.channel_name}">
                            <img src="${data.avatar_url || 'https://api.dicebear.com/7.x/bottts/svg?seed=' + encodeURIComponent(data.channel_name)}" style="width: 36px; height: 36px; border-radius: 50%; border: 1.5px solid var(--gold); box-shadow: 0 0 10px rgba(201,168,76,0.3); flex-shrink: 0;" />
                            <div class="sidebar-text-group" style="flex-grow:1; min-width: 0; display: flex; flex-direction: column;">
                                <span style="color: var(--gold); font-size: 0.82rem; font-weight: 700; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${data.channel_name}</span>
                                <span style="color: var(--ghost); font-size: 0.68rem; font-family:monospace; margin-top: 1px;">${parseInt(data.subscriber_count).toLocaleString()} Subs</span>
                            </div>
                            <button class="btn btn-mini sidebar-text-group" onclick="disconnectYouTube()" style="border-color: #ef4444; color: #ef4444; padding: 2px 6px; font-size: 0.65rem; background: transparent; cursor: pointer; transition: all 0.2s; flex-shrink: 0;" title="Disconnect Account"><i class="fa-solid fa-power-off"></i></button>
                        </div>
                    `;
                } else {
                    box.innerHTML = `
                        <div style="display:flex; align-items:center; gap: 0.75rem; width: 100%;" title="YouTube Disconnected">
                            <div style="width: 36px; height: 36px; border-radius: 50%; border: 1.5px solid var(--wire); display:flex; align-items:center; justify-content:center; background: rgba(255,255,255,0.01); flex-shrink: 0;">
                                <i class="fa-brands fa-youtube" style="color: #ef4444; font-size: 1.1rem;"></i>
                            </div>
                            <div class="sidebar-text-group" style="flex-grow: 1; display:flex; flex-direction:column; min-width:0;">
                                <span style="color: var(--ash); font-size: 0.75rem; font-weight:600;">YouTube Setup</span>
                                <span onclick="connectYouTube()" style="color: #ef4444; font-size: 0.68rem; font-weight:700; cursor:pointer; text-decoration: underline; margin-top:1px;">Link Account</span>
                            </div>
                        </div>
                    `;
                }
            } catch(e) {
                console.error("YouTube status error:", e);
            }
        }
        
        async function checkInstagramStatus() {
            const box = document.getElementById('sidebar-ig-connection');
            try {
                const res = await fetch('/api/instagram/status');
                const data = await res.json();
                
                if (data.connected) {
                    box.innerHTML = `
                        <div style="display:flex; align-items:center; gap: 0.75rem; width: 100%;" title="Instagram Professional ID: ${data.channel_id}">
                            ${data.avatar_url 
                                ? '<img src="' + data.avatar_url + '" style="width: 36px; height: 36px; border-radius: 50%; border: 1.5px solid var(--gold); box-shadow: 0 0 10px rgba(201,168,76,0.3); flex-shrink: 0; object-fit: cover;" />'
                                : '<div style="width: 36px; height: 36px; border-radius: 50%; border: 1.5px solid var(--gold); display:flex; align-items:center; justify-content:center; background: linear-gradient(45deg, #f09433 0%, #e6683c 25%, #dc2743 50%, #cc2366 75%, #bc1888 100%); flex-shrink: 0; box-shadow: 0 0 10px rgba(201,168,76,0.3);"><i class="fa-brands fa-instagram" style="color: white; font-size: 1.2rem;"></i></div>'
                            }
                            <div class="sidebar-text-group" style="flex-grow:1; min-width: 0; display: flex; flex-direction: column;">
                                <span style="color: var(--gold); font-size: 0.82rem; font-weight: 700; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${data.channel_name}</span>
                                <span style="color: var(--ghost); font-size: 0.68rem; font-family:monospace; margin-top: 1px;">${parseInt(data.subscriber_count).toLocaleString()} Followers</span>
                            </div>
                            <button class="btn btn-mini sidebar-text-group" onclick="showToast('Instagram disconnect is managed via .env file currently.', 'info')" style="border-color: #ef4444; color: #ef4444; padding: 2px 6px; font-size: 0.65rem; background: transparent; cursor: pointer; transition: all 0.2s; flex-shrink: 0;" title="Disconnect Info"><i class="fa-solid fa-power-off"></i></button>
                        </div>
                    `;
                } else {
                    box.innerHTML = `
                        <div style="display:flex; align-items:center; gap: 0.75rem; width: 100%;" title="Instagram Disconnected">
                            <div style="width: 36px; height: 36px; border-radius: 50%; border: 1.5px solid var(--wire); display:flex; align-items:center; justify-content:center; background: rgba(255,255,255,0.01); flex-shrink: 0;">
                                <i class="fa-brands fa-instagram" style="color: #ef4444; font-size: 1.1rem;"></i>
                            </div>
                            <div class="sidebar-text-group" style="flex-grow: 1; display:flex; flex-direction:column; min-width:0;">
                                <span style="color: var(--ash); font-size: 0.75rem; font-weight:600;">Instagram Setup</span>
                                <span onclick="showToast('Edit your .env file to add IG_ACCESS_TOKEN and IG_ACCOUNT_ID', 'info')" style="color: #ef4444; font-size: 0.68rem; font-weight:700; cursor:pointer; text-decoration: underline; margin-top:1px;">Missing in .env</span>
                            </div>
                        </div>
                    `;
                }
            } catch(e) {
                console.error("Instagram status error:", e);
            }
        }
        
        async function connectYouTube() {
            try {
                const res = await fetch('/api/youtube/connect');
                if (!res.ok) {
                    const errData = await res.json();
                    showToast("Authentication Error: " + (errData.detail || "Make sure client_secret.json is in the project root."), "error");
                    return;
                }
                const data = await res.json();
                
                // Open auth page in new tab
                window.open(data.auth_url, '_blank');
                showToast("Google Authorization window opened! Please sign in, authorize your channel, and return here when complete.", "info");
                
                // Poll connection status every 3 seconds
                let attempts = 0;
                const statusInterval = setInterval(async () => {
                    attempts++;
                    await checkYouTubeStatus();
                    const currentRes = await fetch('/api/youtube/status');
                    const currentData = await currentRes.json();
                    if (currentData.connected || attempts > 30) {
                        clearInterval(statusInterval);
                    }
                }, 3000);
            } catch(e) {
                showToast("Error connecting YouTube: " + e, "error");
            }
        }

        async function disconnectYouTube() {
            if (!confirm("Are you sure you want to disconnect your YouTube account?")) {
                return;
            }
            try {
                const res = await fetch('/api/youtube/disconnect', { method: 'POST' });
                const data = await res.json();
                showToast(data.message || "Disconnected successfully!", "success");
                await checkYouTubeStatus();
            } catch(e) {
                showToast("Error disconnecting YouTube: " + e, "error");
            }
        }


        // Manual clipper controls
        let lastFetchedUrl = "";
        let fetchedDetailsCache = null;

        function isPlaceholderCache(cache) {
            if (!cache) return true;
            const title = cache.title || "";
            const thumbnail = cache.thumbnail || "";
            return (
                title === "YouTube Video" ||
                title === "YouTube Video Loaded (Ready to Analyze)" ||
                title === "Fetching video details..." ||
                !thumbnail ||
                thumbnail === "" ||
                thumbnail.includes("unsplash.com")
            );
        }

        async function fetchVideoDetails(url) {
            if (url !== lastFetchedUrl) {
                fetchedDetailsCache = null;
            }
            if (url === lastFetchedUrl && fetchedDetailsCache && !isPlaceholderCache(fetchedDetailsCache)) {
                return fetchedDetailsCache;
            }
            lastFetchedUrl = url;

            const detailsCard = document.getElementById('interactive-video-card');
            const titleEl = document.getElementById('inter-video-title');
            const thumbEl = document.getElementById('inter-video-thumb');
            const urlEl = document.getElementById('inter-video-url');

            // Show details card with loading state
            detailsCard.style.display = 'flex';
            titleEl.textContent = "Fetching video details...";
            thumbEl.src = "https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=320";
            urlEl.textContent = url;

            try {
                const res = await fetch('/api/scout/video-details', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: url })
                });
                if (res.ok) {
                    const data = await res.json();
                    if (!isPlaceholderCache(data)) {
                        titleEl.textContent = data.title;
                        thumbEl.src = data.thumbnail || "https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=320";
                        fetchedDetailsCache = data;
                        
                        // Update localStorage details if matching URL exists
                        const storedUrl = localStorage.getItem('interactive-manual-url');
                        if (storedUrl === url) {
                            const storedResultsStr = localStorage.getItem('interactive-manual-results');
                            if (storedResultsStr) {
                                try {
                                    const saved = JSON.parse(storedResultsStr);
                                    saved.details = data;
                                    localStorage.setItem('interactive-manual-results', JSON.stringify(saved));
                                } catch (e) {
                                    console.error("Failed to update localStorage with real details", e);
                                }
                            }
                        }
                        return data;
                    }
                }
                
                titleEl.textContent = "YouTube Video Loaded (Ready to Analyze)";
                fetchedDetailsCache = { title: "YouTube Video", thumbnail: "" };
                return fetchedDetailsCache;
            } catch (err) {
                console.error("Error fetching video details:", err);
                titleEl.textContent = "YouTube Video Loaded (Ready to Analyze)";
                fetchedDetailsCache = { title: "YouTube Video", thumbnail: "" };
                return fetchedDetailsCache;
            }
        }

        // Manual clipper controls
        async function analyzeVideoInteractive() {
            const url = document.getElementById('manual-url').value.trim();
            if (!url) {
                showToast("Please enter a valid YouTube URL first!", "warning");
                return;
            }
            
            currentUrlTranscript = url;
            
            const loadingPanel = document.getElementById('clipper-loading-panel');
            const loadingText = document.getElementById('clipper-loading-text');
            const resultsPanel = document.getElementById('clipper-results-panel');
            
            loadingPanel.style.display = 'block';
            resultsPanel.style.display = 'none';
            
            try {
                // Ensure video details are fetched/loaded
                const details = await fetchVideoDetails(url);
                
                // Step 2: Download/Get Transcript
                loadingText.textContent = "[1/2] Fetching or transcribing rough audio sample (may take 20-30s)...";
                const transcriptRes = await fetch('/api/scout/transcript', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: url })
                });
                
                if (!transcriptRes.ok) {
                    const errData = await transcriptRes.json();
                    throw new Error(errData.detail || "Failed to retrieve transcript");
                }
                
                const transcriptData = await transcriptRes.json();
                currentSegments = transcriptData.segments;
                
                // Step 3: Consulting Gemini to Select Top Highlights
                loadingText.textContent = "[2/2] AI Ranking viral clips using Gemini Thinking engine...";
                const highlightsRes = await fetch('/api/scout/highlights', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        segments: currentSegments,
                        count: 5 // Limit to maximum 5
                    })
                });
                
                if (!highlightsRes.ok) {
                    const errData = await highlightsRes.json();
                    throw new Error(errData.detail || "Failed to generate highlights");
                }
                
                const highlightsData = await highlightsRes.json();
                
                // Populate Cards Grid
                const grid = document.getElementById('highlights-cards-grid');
                grid.innerHTML = "";
                
                if (!highlightsData.highlights || !highlightsData.highlights.length) {
                    grid.innerHTML = '<div style="text-align:center; padding: 2rem; color: var(--ghost); font-weight:600;">No viral clips found above the score threshold.</div>';
                } else {
                    const thumbUrl = (details && details.thumbnail) || "https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=320";
                    
                    highlightsData.highlights.slice(0, 5).forEach((hl, index) => {
                        const card = document.createElement('div');
                        card.className = 'glass-panel';
                        card.style.padding = '1.25rem';
                        card.style.marginBottom = '0';
                        card.style.borderColor = 'var(--wire)';
                        card.style.display = 'flex';
                        card.style.flexDirection = 'column';
                        card.style.gap = '1rem';
                        card.style.position = 'relative';

                        const formatTime = (sec) => {
                            const m = Math.floor(sec / 60);
                            const s = Math.floor(sec % 60);
                            return `${m}:${s < 10 ? '0' : ''}${s}`;
                        };

                        card.innerHTML = `
                            <div style="display: flex; gap: 1.25rem; align-items: flex-start; flex-wrap: wrap;">
                                <!-- Clip Thumbnail Area with Time Badges -->
                                <div style="position: relative; width: 140px; height: 90px; flex-shrink: 0; border-radius: 8px; overflow: hidden; border: 1px solid var(--wire); background: var(--ink);">
                                    <img src="${thumbUrl}" style="width: 100%; height: 100%; object-fit: cover;" />
                                    <span style="position: absolute; bottom: 5px; right: 5px; background: rgba(0,0,0,0.85); color: #fff; padding: 2px 6px; border-radius: 4px; font-size: 0.65rem; font-family: monospace; font-weight: 700; z-index: 5;">
                                        ${formatTime(hl.start)} - ${formatTime(hl.end)}
                                    </span>
                                    <div onclick="openYoutubePreviewModal('${(hl.title || 'Clip Highlight').replace(/'/g, "\\'")}', '${currentUrlTranscript}', ${hl.start}, ${hl.end}, ${index})" style="position: absolute; top: 0; left: 0; right: 0; bottom: 0; display: flex; align-items: center; justify-content: center; background: rgba(0,0,0,0.25); cursor: pointer; transition: all 0.2s;" onmouseover="this.style.background='rgba(0,0,0,0.55)'" onmouseout="this.style.background='rgba(0,0,0,0.25)'" title="Preview Segment Clip">
                                        <i class="fa-solid fa-circle-play" style="color: #fff; text-shadow: 0 0 8px rgba(0,0,0,0.9); font-size: 1.8rem; transition: transform 0.2s;" onmouseover="this.style.transform='scale(1.15)'" onmouseout="this.style.transform='scale(1)'"></i>
                                    </div>
                                </div>
                                
                                <!-- Clip Info & Actions -->
                                <div style="flex: 1; min-width: 250px; display: flex; flex-direction: column; gap: 0.4rem;">
                                    <div style="display:flex; justify-content:space-between; align-items:center; border-bottom: 1px solid var(--wire); padding-bottom: 0.4rem; gap: 0.5rem; flex-wrap: wrap;">
                                        <h4 style="color: var(--gold); font-weight: 800; font-size: 0.95rem; margin: 0; line-height: 1.2;">
                                            ${hl.title || ('Clip Highlight #' + (index + 1))}
                                        </h4>
                                        <div style="display:flex; align-items:center; gap: 0.5rem;">
                                            <span class="badge" style="background: rgba(0, 210, 255, 0.1); color: var(--cyan); padding: 2px 6px; border-radius: 4px; font-size:0.7rem; font-weight:600;"><i class="fa-solid fa-crop"></i> ${hl.layout}</span>
                                            <span class="badge" style="background: rgba(52, 211, 153, 0.1); color: #34d399; padding: 2px 6px; border-radius: 4px; font-size:0.7rem; font-weight:600;"><i class="fa-solid fa-chart-line"></i> Virality: ${hl.virality_score}%</span>
                                        </div>
                                    </div>
                                    
                                    ${hl.strongest_hook_line ? `
                                    <div style="background: rgba(255, 255, 255, 0.01); border-left: 3px solid var(--gold); padding: 0.35rem 0.75rem; border-radius: 4px; font-style: italic; font-size:0.8rem; color: var(--smoke);">
                                        "${hl.strongest_hook_line}"
                                    </div>` : ''}
                                    
                                    <div style="font-size:0.8rem; color: var(--ghost); line-height: 1.4;">
                                        <strong>Why it works:</strong> ${hl.reason}
                                    </div>

                                    <!-- Dynamic Crop Customizer -->
                                    <div style="display: flex; gap: 1rem; align-items: center; background: rgba(255, 255, 255, 0.02); border: 1px solid var(--wire); border-radius: 6px; padding: 0.5rem 0.75rem; margin-top: 0.25rem; flex-wrap: wrap;">
                                        <div style="display: flex; align-items: center; gap: 0.5rem;">
                                            <label style="font-size: 0.78rem; color: var(--smoke); font-weight: 600; white-space: nowrap;"><i class="fa-solid fa-crop" style="color: var(--cyan);"></i> Layout:</label>
                                            <select id="crop-layout-select-${index}" onchange="updateCropSelection(${index})" style="padding: 0.25rem 0.5rem; font-size: 0.78rem; border-radius: 4px; border: 1px solid var(--wire); background: var(--ink); color: var(--smoke);">
                                                <option value="crop_center" ${hl.layout === 'crop_center' ? 'selected' : ''}>Center Crop</option>
                                                <option value="crop_left" ${hl.layout === 'crop_left' ? 'selected' : ''}>Left Crop</option>
                                                <option value="crop_right" ${hl.layout === 'crop_right' ? 'selected' : ''}>Right Crop</option>
                                                <option value="auto_track" ${hl.layout === 'auto_track' ? 'selected' : ''}>AI Face Track (Static)</option>
                                                <option value="auto_pan" ${hl.layout === 'auto_pan' ? 'selected' : ''}>AI Face Pan (Ken Burns)</option>
                                                <option value="custom_offset" ${String(hl.layout).startsWith('custom_offset_') ? 'selected' : ''}>Custom Offset</option>
                                            </select>
                                        </div>
                                        
                                        <div id="crop-offset-wrapper-${index}" style="display: ${String(hl.layout).startsWith('custom_offset_') ? 'flex' : 'none'}; align-items: center; gap: 0.5rem; flex: 1; min-width: 180px;">
                                            <input type="range" id="crop-offset-slider-${index}" min="-300" max="300" value="${String(hl.layout).startsWith('custom_offset_') ? hl.layout.split('_').pop() : '0'}" oninput="updateCropOffsetLabel(${index}, this.value)" style="flex: 1; accent-color: var(--cyan); cursor: pointer; height: 5px; border-radius: 3px;">
                                            <span id="crop-offset-label-${index}" style="font-family: monospace; font-size: 0.75rem; color: var(--cyan); min-width: 45px; text-align: right;">${String(hl.layout).startsWith('custom_offset_') ? hl.layout.split('_').pop() : '0'}px</span>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <!-- Bottom Row Controls -->
                            <div style="display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; border-top: 1px solid var(--wire); padding-top: 0.75rem; margin-top: 0.25rem; gap: 1rem;">
                                <div style="display:flex; align-items:center; gap:1.25rem;">
                                    <label style="display:inline-flex; align-items:center; gap:0.5rem; cursor:pointer; font-size:0.8rem; color: var(--smoke);">
                                        <input type="checkbox" id="post-check-${index}" checked style="width: 15px; height: 15px; cursor:pointer;">
                                        <span>Auto-Publish when rendered</span>
                                    </label>
                                    <select id="post-privacy-${index}" style="padding: 0.25rem 0.5rem; font-size: 0.78rem; border-radius: 4px; border:1px solid var(--wire); background: var(--ink); color: var(--smoke); cursor: pointer;">
                                        <option value="private" selected>🔒 Private</option>
                                        <option value="unlisted">🔗 Unlisted</option>
                                        <option value="public">🌍 Public</option>
                                    </select>
                                </div>

                                <div style="display:flex; align-items:center; gap:0.5rem;">
                                    <button class="btn btn-mini btn-secondary" onclick="openYoutubePreviewModal('${(hl.title || 'Clip Highlight').replace(/'/g, "\\'")}', '${currentUrlTranscript}', ${hl.start}, ${hl.end}, ${index})">
                                        <i class="fa-solid fa-circle-play"></i> Preview Clip
                                    </button>
                                    <button class="btn btn-mini btn-cyan" id="btn-render-hl-${index}" onclick="renderDetailedHighlight(${index}, ${hl.start}, ${hl.end}, '${hl.layout}')">
                                        <i class="fa-solid fa-play"></i> Render & Publish
                                    </button>
                                </div>
                            </div>
                        `;
                        grid.appendChild(card);
                    });
                }
                
                resultsPanel.style.display = 'block';
                
                // Store results in localStorage for persistence
                localStorage.setItem('interactive-manual-url', url);
                localStorage.setItem('interactive-manual-results', JSON.stringify({
                    details: details,
                    highlights: highlightsData.highlights
                }));
            } catch (err) {
                showToast("Analysis failed: " + err.message, "error");
            } finally {
                loadingPanel.style.display = 'none';
            }
        }

        async function pasteClipboardUrl() {
            try {
                const text = await navigator.clipboard.readText();
                if (text) {
                    document.getElementById('manual-url').value = text;
                    const event = new Event('input', { bubbles: true });
                    document.getElementById('manual-url').dispatchEvent(event);
                }
            } catch (err) {
                console.error("Failed to read clipboard:", err);
                showToast("Please allow clipboard permissions or paste manually.", "warning");
            }
        }

        function clearManualClipper() {
            document.getElementById('manual-url').value = "";
            document.getElementById('interactive-video-card').style.display = 'none';
            document.getElementById('clipper-results-panel').style.display = 'none';
            document.getElementById('clipper-loading-panel').style.display = 'none';
            document.getElementById('inter-video-title').textContent = "";
            document.getElementById('inter-video-thumb').src = "";
            document.getElementById('inter-video-url').textContent = "";
            document.getElementById('highlights-cards-grid').innerHTML = "";
            
            lastFetchedUrl = "";
            fetchedDetailsCache = null;
            currentUrlTranscript = "";
            currentSegments = [];
            
            localStorage.removeItem('interactive-manual-url');
            localStorage.removeItem('interactive-manual-results');
        }

        function restoreInteractiveClipperState() {
            const storedUrl = localStorage.getItem('interactive-manual-url');
            const storedResultsStr = localStorage.getItem('interactive-manual-results');
            if (storedUrl && storedResultsStr) {
                try {
                    const saved = JSON.parse(storedResultsStr);
                    document.getElementById('manual-url').value = storedUrl;
                    currentUrlTranscript = storedUrl;
                    
                    // Restore Cache
                    fetchedDetailsCache = saved.details;
                    lastFetchedUrl = storedUrl;
                    
                    // Render details card
                    const detailsCard = document.getElementById('interactive-video-card');
                    const titleEl = document.getElementById('inter-video-title');
                    const thumbEl = document.getElementById('inter-video-thumb');
                    const urlEl = document.getElementById('inter-video-url');
                    
                    detailsCard.style.display = 'flex';
                    titleEl.textContent = saved.details.title;
                    thumbEl.src = saved.details.thumbnail || "https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=320";
                    urlEl.textContent = storedUrl;

                    // If restored cache is a placeholder, trigger a fresh fetch in the background
                    if (isPlaceholderCache(saved.details)) {
                        fetchVideoDetails(storedUrl).catch(e => console.error("Background restore fetch failed:", e));
                    }
                    
                    // Restore Highlights Grid
                    const grid = document.getElementById('highlights-cards-grid');
                    grid.innerHTML = "";
                    const resultsPanel = document.getElementById('clipper-results-panel');
                    
                    if (saved.highlights && saved.highlights.length) {
                        const thumbUrl = saved.details.thumbnail || "https://images.unsplash.com/photo-1611162617213-7d7a39e9b1d7?w=320";
                        
                        saved.highlights.forEach((hl, index) => {
                            const card = document.createElement('div');
                            card.className = 'glass-panel';
                            card.style.padding = '1.25rem';
                            card.style.marginBottom = '0';
                            card.style.borderColor = 'var(--wire)';
                            card.style.display = 'flex';
                            card.style.flexDirection = 'column';
                            card.style.gap = '1rem';
                            card.style.position = 'relative';

                            const formatTime = (sec) => {
                                const m = Math.floor(sec / 60);
                                const s = Math.floor(sec % 60);
                                return `${m}:${s < 10 ? '0' : ''}${s}`;
                            };

                            card.innerHTML = `
                                <div style="display: flex; gap: 1.25rem; align-items: flex-start; flex-wrap: wrap;">
                                    <!-- Clip Thumbnail Area with Time Badges -->
                                    <div style="position: relative; width: 140px; height: 90px; flex-shrink: 0; border-radius: 8px; overflow: hidden; border: 1px solid var(--wire); background: var(--ink);">
                                        <img src="${thumbUrl}" style="width: 100%; height: 100%; object-fit: cover;" />
                                        <span style="position: absolute; bottom: 5px; right: 5px; background: rgba(0,0,0,0.85); color: #fff; padding: 2px 6px; border-radius: 4px; font-size: 0.65rem; font-family: monospace; font-weight: 700; z-index: 5;">
                                            ${formatTime(hl.start)} - ${formatTime(hl.end)}
                                        </span>
                                        <div onclick="openYoutubePreviewModal('${(hl.title || 'Clip Highlight').replace(/'/g, "\\'")}', '${storedUrl}', ${hl.start}, ${hl.end}, ${index})" style="position: absolute; top: 0; left: 0; right: 0; bottom: 0; display: flex; align-items: center; justify-content: center; background: rgba(0,0,0,0.25); cursor: pointer; transition: all 0.2s;" onmouseover="this.style.background='rgba(0,0,0,0.55)'" onmouseout="this.style.background='rgba(0,0,0,0.25)'" title="Preview Segment Clip">
                                            <i class="fa-solid fa-circle-play" style="color: #fff; text-shadow: 0 0 8px rgba(0,0,0,0.9); font-size: 1.8rem; transition: transform 0.2s;" onmouseover="this.style.transform='scale(1.15)'" onmouseout="this.style.transform='scale(1)'"></i>
                                        </div>
                                    </div>
                                    
                                    <!-- Clip Info & Actions -->
                                    <div style="flex: 1; min-width: 250px; display: flex; flex-direction: column; gap: 0.4rem;">
                                        <div style="display:flex; justify-content:space-between; align-items:center; border-bottom: 1px solid var(--wire); padding-bottom: 0.4rem; gap: 0.5rem; flex-wrap: wrap;">
                                            <h4 style="color: var(--gold); font-weight: 800; font-size: 0.95rem; margin: 0; line-height: 1.2;">
                                                ${hl.title || ('Clip Highlight #' + (index + 1))}
                                            </h4>
                                            <div style="display:flex; align-items:center; gap: 0.5rem;">
                                                <span class="badge" style="background: rgba(0, 210, 255, 0.1); color: var(--cyan); padding: 2px 6px; border-radius: 4px; font-size:0.7rem; font-weight:600;"><i class="fa-solid fa-crop"></i> ${hl.layout}</span>
                                                <span class="badge" style="background: rgba(52, 211, 153, 0.1); color: #34d399; padding: 2px 6px; border-radius: 4px; font-size:0.7rem; font-weight:600;"><i class="fa-solid fa-chart-line"></i> Virality: ${hl.virality_score}%</span>
                                            </div>
                                        </div>
                                        
                                        ${hl.strongest_hook_line ? `
                                        <div style="background: rgba(255, 255, 255, 0.01); border-left: 3px solid var(--gold); padding: 0.35rem 0.75rem; border-radius: 4px; font-style: italic; font-size:0.8rem; color: var(--smoke);">
                                            "${hl.strongest_hook_line}"
                                        </div>` : ''}
                                        
                                        <div style="font-size:0.8rem; color: var(--ghost); line-height: 1.4;">
                                            <strong>Why it works:</strong> ${hl.reason}
                                        </div>

                                        <!-- Dynamic Crop Customizer -->
                                        <div style="display: flex; gap: 1rem; align-items: center; background: rgba(255, 255, 255, 0.02); border: 1px solid var(--wire); border-radius: 6px; padding: 0.5rem 0.75rem; margin-top: 0.25rem; flex-wrap: wrap;">
                                            <div style="display: flex; align-items: center; gap: 0.5rem;">
                                                <label style="font-size: 0.78rem; color: var(--smoke); font-weight: 600; white-space: nowrap;"><i class="fa-solid fa-crop" style="color: var(--cyan);"></i> Layout:</label>
                                                <select id="crop-layout-select-${index}" onchange="updateCropSelection(${index})" style="padding: 0.25rem 0.5rem; font-size: 0.78rem; border-radius: 4px; border: 1px solid var(--wire); background: var(--ink); color: var(--smoke);">
                                                    <option value="crop_center" ${hl.layout === 'crop_center' ? 'selected' : ''}>Center Crop</option>
                                                    <option value="crop_left" ${hl.layout === 'crop_left' ? 'selected' : ''}>Left Crop</option>
                                                    <option value="crop_right" ${hl.layout === 'crop_right' ? 'selected' : ''}>Right Crop</option>
                                                    <option value="auto_track" ${hl.layout === 'auto_track' ? 'selected' : ''}>AI Face Track (Static)</option>
                                                    <option value="auto_pan" ${hl.layout === 'auto_pan' ? 'selected' : ''}>AI Face Pan (Ken Burns)</option>
                                                    <option value="custom_offset" ${String(hl.layout).startsWith('custom_offset_') ? 'selected' : ''}>Custom Offset</option>
                                                </select>
                                            </div>
                                            
                                            <div id="crop-offset-wrapper-${index}" style="display: ${String(hl.layout).startsWith('custom_offset_') ? 'flex' : 'none'}; align-items: center; gap: 0.5rem; flex: 1; min-width: 180px;">
                                                <input type="range" id="crop-offset-slider-${index}" min="-300" max="300" value="${String(hl.layout).startsWith('custom_offset_') ? hl.layout.split('_').pop() : '0'}" oninput="updateCropOffsetLabel(${index}, this.value)" style="flex: 1; accent-color: var(--cyan); cursor: pointer; height: 5px; border-radius: 3px;">
                                                <span id="crop-offset-label-${index}" style="font-family: monospace; font-size: 0.75rem; color: var(--cyan); min-width: 45px; text-align: right;">${String(hl.layout).startsWith('custom_offset_') ? hl.layout.split('_').pop() : '0'}px</span>
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                <!-- Bottom Row Controls -->
                                <div style="display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; border-top: 1px solid var(--wire); padding-top: 0.75rem; margin-top: 0.25rem; gap: 1rem;">
                                    <div style="display:flex; align-items:center; gap:1.25rem;">
                                        <label style="display:inline-flex; align-items:center; gap:0.5rem; cursor:pointer; font-size:0.8rem; color: var(--smoke);">
                                            <input type="checkbox" id="post-check-${index}" checked style="width: 15px; height: 15px; cursor:pointer;">
                                            <span>Auto-Publish when rendered</span>
                                        </label>
                                        <select id="post-privacy-${index}" style="padding: 0.25rem 0.5rem; font-size: 0.78rem; border-radius: 4px; border:1px solid var(--wire); background: var(--ink); color: var(--smoke); cursor: pointer;">
                                            <option value="private" selected>🔒 Private</option>
                                            <option value="unlisted">🔗 Unlisted</option>
                                            <option value="public">🌍 Public</option>
                                        </select>
                                    </div>

                                    <div style="display:flex; align-items:center; gap:0.5rem;">
                                        <button class="btn btn-mini btn-secondary" onclick="openYoutubePreviewModal('${(hl.title || 'Clip Highlight').replace(/'/g, "\\'")}', '${storedUrl}', ${hl.start}, ${hl.end}, ${index})">
                                            <i class="fa-solid fa-circle-play"></i> Preview Clip
                                        </button>
                                        <button class="btn btn-mini btn-cyan" id="btn-render-hl-${index}" onclick="renderDetailedHighlight(${index}, ${hl.start}, ${hl.end}, '${hl.layout}')">
                                            <i class="fa-solid fa-play"></i> Render & Publish
                                        </button>
                                    </div>
                                </div>
                            `;
                            grid.appendChild(card);
                        });
                        resultsPanel.style.display = 'block';
                    }
                } catch (e) {
                    console.error("Failed to restore manual clipper state:", e);
                }
            }
        }

        async function renderDetailedHighlight(index, start, end, layout) {
            const upload = document.getElementById(`post-check-${index}`).checked;
            const privacy = document.getElementById(`post-privacy-${index}`).value;
            
            const btn = document.getElementById(`btn-render-hl-${index}`);
            const originalHTML = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin"></i> Rendering...`;
            
            try {
                const res = await fetch('/api/clip/render', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: currentUrlTranscript,
                        start: start,
                        end: end,
                        layout: layout,
                        upload: upload,
                        privacy: privacy
                    })
                });
                
                if (!res.ok) {
                    const errData = await res.json();
                    throw new Error(errData.detail || "Render trigger failed");
                }
                
                const data = await res.json();
                showToast("Manual clipping task triggered. Track it in the progress bar!", "success");
                if (data.job_id) {
                    trackJobProgress(data.job_id);
                }
            } catch (err) {
                showToast("Failed to render highlight: " + err.message, "error");
            } finally {
                btn.disabled = false;
                btn.innerHTML = originalHTML;
            }
        }

        let currentLibraryTab = 'local';
        let lastUploadPcts = {}; // {clipName: {pct: Number, time: Number}}
        let libraryPollInterval = null;

        function switchLibraryTab(tabName) {
            currentLibraryTab = tabName;
            const localBtn = document.getElementById('tab-library-local');
            const publishedBtn = document.getElementById('tab-library-published');
            if (tabName === 'local') {
                localBtn.style.color = 'var(--gold)';
                localBtn.style.borderBottom = '2px solid var(--gold)';
                localBtn.style.fontWeight = 'bold';
                publishedBtn.style.color = 'var(--smoke)';
                publishedBtn.style.borderBottom = 'none';
                publishedBtn.style.fontWeight = '500';
            } else {
                publishedBtn.style.color = 'var(--gold)';
                publishedBtn.style.borderBottom = '2px solid var(--gold)';
                publishedBtn.style.fontWeight = 'bold';
                localBtn.style.color = 'var(--smoke)';
                localBtn.style.borderBottom = 'none';
                localBtn.style.fontWeight = '500';
            }
            refreshLibrary();
        }

        // Library video operations
        async function refreshLibrary() {
            const container = document.getElementById('library-clips-container');
            const uploadContainer = document.getElementById('active-uploading-container');
            
            try {
                const res = await fetch('/api/clips');
                const clips = await res.json();
                
                // Update total count badge
                document.getElementById('stat-total-clips').textContent = clips.length;
                
                // Track active uploading state for real-time progress card
                let uploadingClip = null;
                clips.forEach(clip => {
                    const meta = clip.metadata || {};
                    if (meta.publish_status === 'uploading') {
                        uploadingClip = clip;
                    }
                });
                
                if (uploadingClip) {
                    uploadContainer.style.display = 'block';
                    const pct = uploadingClip.metadata.publish_progress || 0;
                    const now = Date.now();
                    const size = uploadingClip.size;
                    
                    let speedStr = "Estimating speed...";
                    let etaStr = "Calculating remaining time...";
                    
                    if (lastUploadPcts[uploadingClip.name]) {
                        const lastVal = lastUploadPcts[uploadingClip.name];
                        const elapsedSec = (now - lastVal.time) / 1000;
                        const pctDiff = pct - lastVal.pct;
                        
                        if (elapsedSec > 0.5 && pctDiff > 0) {
                            const speedBps = (size * (pctDiff / 100)) / elapsedSec;
                            const speedMBps = speedBps / (1024 * 1024);
                            speedStr = `${speedMBps.toFixed(1)} MB/s`;
                            
                            const remainingBytes = size * (1 - pct / 100);
                            const etaSec = remainingBytes / speedBps;
                            if (etaSec > 60) {
                                etaStr = `${Math.floor(etaSec / 60)}m ${Math.floor(etaSec % 60)}s remaining`;
                            } else {
                                etaStr = `${Math.floor(etaSec)}s remaining`;
                            }
                        }
                    }
                    
                    // Update cache
                    if (!lastUploadPcts[uploadingClip.name] || lastUploadPcts[uploadingClip.name].pct !== pct) {
                        lastUploadPcts[uploadingClip.name] = { pct: pct, time: now };
                    }
                    
                    const titleText = uploadingClip.metadata.title || uploadingClip.name;
                    uploadContainer.innerHTML = `
                        <div style="background: rgba(201,168,76,0.04); border: 1.5px solid var(--gold); border-radius: 8px; padding: 1.25rem; box-shadow: 0 0 15px rgba(201,168,76,0.15); animation: pulse 2s infinite;">
                            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.6rem;">
                                <span style="color: var(--gold); font-weight: bold; font-size: 0.88rem;"><i class="fa-solid fa-cloud-arrow-up fa-bounce" style="margin-right: 0.4rem;"></i> ACTIVELY UPLOADING SHORTS</span>
                                <span style="color: var(--gold); font-family: monospace; font-weight: bold; font-size: 0.88rem;">${pct}%</span>
                            </div>
                            <div style="color: var(--smoke); font-size: 0.85rem; font-weight: 600; margin-bottom: 0.5rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${titleText}</div>
                            <div style="width: 100%; height: 8px; background: rgba(255,255,255,0.05); border-radius: 4px; overflow: hidden; margin-bottom: 0.6rem; border: 1px solid var(--wire);">
                                <div style="width: ${pct}%; height: 100%; background: linear-gradient(90deg, var(--gold), var(--cyan)); transition: width 0.4s ease-out; box-shadow: 0 0 8px var(--gold);"></div>
                            </div>
                            <div style="display: flex; justify-content: space-between; font-size: 0.72rem; color: var(--ghost); font-family: monospace;">
                                <span>Size: ${(size / (1024*1024)).toFixed(1)} MB</span>
                                <span>Speed: ${speedStr}</span>
                                <span>ETA: ${etaStr}</span>
                            </div>
                        </div>
                    `;
                    
                    // Manage polling
                    if (!libraryPollInterval) {
                        libraryPollInterval = setInterval(refreshLibrary, 2000);
                    }
                } else {
                    uploadContainer.innerHTML = "";
                    uploadContainer.style.display = 'none';
                    if (libraryPollInterval) {
                        clearInterval(libraryPollInterval);
                        libraryPollInterval = null;
                    }
                }
                
                // Filter clips based on current active tab
                let filteredClips = clips;
                if (currentLibraryTab === 'local') {
                    filteredClips = clips.filter(c => !c.metadata || (c.metadata.publish_status !== 'success' && c.metadata.publish_status !== 'partial_success'));
                } else {
                    filteredClips = clips.filter(c => c.metadata && (c.metadata.publish_status === 'success' || c.metadata.publish_status === 'partial_success'));
                }
                
                // Prevent focus-stealing re-renders by signature matching
                const clipSig = JSON.stringify(filteredClips.map(c => ({
                    name: c.name, size: c.size, thumbnail: c.thumbnail,
                    status: c.metadata ? c.metadata.publish_status : null,
                    title: c.metadata ? c.metadata.title : null,
                    yt_id: c.metadata ? c.metadata.youtube_video_id : null,
                    ig_id: c.metadata ? c.metadata.instagram_video_id : null
                })));
                
                if (window.lastClipSig === clipSig && container.innerHTML !== "") {
                    // Update only badges for uploading clips in place
                    filteredClips.forEach(clip => {
                        const badgeEl = document.getElementById('badge-' + clip.name);
                        if (badgeEl && clip.metadata && clip.metadata.publish_status === 'uploading') {
                            badgeEl.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin"></i> UPLOADING (${clip.metadata.publish_progress || 0}%)`;
                        }
                    });
                    return;
                }
                window.lastClipSig = clipSig;

                container.innerHTML = "";
                if (!filteredClips.length) {
                    if (currentLibraryTab === 'local') {
                        container.innerHTML = '<div style="grid-column: span 4; text-align:center; color:var(--text-muted); padding: 3rem 0;">No local draft clips left! Auto-scout or render more clips above.</div>';
                    } else {
                        container.innerHTML = '<div style="grid-column: span 4; text-align:center; color:var(--text-muted); padding: 3rem 0;">No clips have been published yet. Pick a draft video and hit "Publish"!</div>';
                    }
                    return;
                }
                
                filteredClips.forEach(clip => {
                    const card = document.createElement('div');
                    card.className = 'clip-card';
                    
                    const formatSize = (bytes) => {
                        const mb = bytes / (1024 * 1024);
                        return `${mb.toFixed(1)} MB`;
                    };
                    
                    const formatDate = (isoStr) => {
                        const d = new Date(isoStr);
                        return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
                    };

                    const getPublishBadge = (meta, clipName) => {
                        if (!meta) return '';
                        if (meta.publish_status === 'success') {
                            return `<span id="badge-${clipName}" class="badge" style="font-size: 0.72rem; background: rgba(16,185,129,0.15); color: #10b981; padding: 2px 6px; border-radius: 4px; display: inline-flex; align-items: center; gap: 4px; font-weight:600;"><i class="fa-solid fa-check-circle"></i> PUBLISHED</span>`;
                        }
                        if (meta.publish_status === 'partial_success') {
                            return `<span id="badge-${clipName}" class="badge" style="font-size: 0.72rem; background: rgba(245,158,11,0.15); color: #f59e0b; padding: 2px 6px; border-radius: 4px; display: inline-flex; align-items: center; gap: 4px; font-weight:600; cursor: help;" title="${meta.publish_error || 'Partial upload'}"><i class="fa-solid fa-triangle-exclamation"></i> PARTIAL</span>`;
                        }
                        if (meta.publish_status === 'uploading') {
                            return `<span id="badge-${clipName}" class="badge" style="font-size: 0.72rem; background: rgba(201,168,76,0.15); color: var(--gold); padding: 2px 6px; border-radius: 4px; display: inline-flex; align-items: center; gap: 4px; font-weight:600;"><i class="fa-solid fa-circle-notch fa-spin"></i> UPLOADING (${meta.publish_progress || 0}%)</span>`;
                        }
                        if (meta.publish_status === 'failed') {
                            return `<span id="badge-${clipName}" class="badge" style="font-size: 0.72rem; background: rgba(239,68,68,0.15); color: #ef4444; padding: 2px 6px; border-radius: 4px; display: inline-flex; align-items: center; gap: 4px; font-weight:600; cursor: help;" title="${meta.publish_error || 'Upload failed'}"><i class="fa-solid fa-triangle-exclamation"></i> FAILED</span>`;
                        }
                        return '';
                    };

                    const metadata = clip.metadata || {};
                    const titleText = metadata.title || clip.name;
                    
                    const actionPanel = currentLibraryTab === 'local' ? `
                        <div class="clip-publish-actions" style="padding: 0.5rem 0.75rem 0.75rem 0.75rem; border-top: 1px solid rgba(255,255,255,0.03); display: flex; flex-direction: column; gap: 0.5rem;">
                            <div style="display: flex; gap: 0.5rem; width: 100%;">
                                <select id="privacy-select-${clip.name}" style="background: var(--ink); border: 1px solid var(--wire); color: var(--smoke); font-size: 0.75rem; border-radius: 4px; padding: 4px; flex: 1; cursor: pointer;">
                                    <option value="private" selected>🔒 Private</option>
                                    <option value="unlisted">🔗 Unlisted</option>
                                    <option value="public">🌍 Public</option>
                                </select>
                            </div>
                            <div style="display: flex; gap: 0.5rem; width: 100%;">
                                <button class="btn btn-mini btn-secondary" onclick="publishClip('${clip.name}')" style="color: var(--gold); border-color: var(--gold); flex: 1;" ${metadata.publish_status === 'uploading' ? 'disabled' : ''}><i class="fa-solid fa-cloud-arrow-up" style="margin-right: 0.25rem;"></i> Publish</button>
                                <button class="btn btn-mini btn-secondary" onclick="deleteClip('${clip.name}')" style="color: #ef4444; border-color: #ef4444;" ${metadata.publish_status === 'uploading' ? 'disabled' : ''}><i class="fa-solid fa-trash"></i></button>
                            </div>
                        </div>
                    ` : `
                        <div class="clip-publish-actions" style="padding: 0.5rem 0.75rem 0.75rem 0.75rem; border-top: 1px solid rgba(255,255,255,0.03); display: flex; gap: 0.5rem;">
                            ${metadata.youtube_video_id ? `<a class="btn btn-secondary btn-mini" href="https://www.youtube.com/watch?v=\${metadata.youtube_video_id}" target="_blank" style="color: var(--gold); border-color: var(--gold); flex: 1; text-align: center; display: flex; align-items: center; justify-content: center; text-decoration: none;"><i class="fa-brands fa-youtube" style="margin-right: 0.25rem;"></i> YouTube</a>` : ''}
                            ${metadata.instagram_video_id ? `<a class="btn btn-secondary btn-mini" href="https://www.instagram.com/reel/\${metadata.instagram_video_id}/" target="_blank" style="color: #E1306C; border-color: #E1306C; flex: 1; text-align: center; display: flex; align-items: center; justify-content: center; text-decoration: none;"><i class="fa-brands fa-instagram" style="margin-right: 0.25rem;"></i> Instagram</a>` : ''}
                            ${(!metadata.youtube_video_id && !metadata.instagram_video_id) ? `<span style="color: #10b981; font-size: 0.75rem; flex: 1; text-align: center; display: flex; align-items: center; justify-content: center;">Published</span>` : ''}
                            <button class="btn btn-mini btn-secondary" onclick="deleteClip('\${clip.name}')" style="color: #ef4444; border-color: #ef4444; flex-shrink: 0;"><i class="fa-solid fa-trash"></i></button>
                        </div>
                    `;

                    card.innerHTML = `
                        <div class="clip-preview-placeholder" style="${clip.thumbnail ? `background-image: url('${clip.thumbnail}'); background-size: cover; background-position: center;` : ''}">
                            <div class="clip-overlay-play" onclick="openVideoModal('${clip.name}', '${clip.path}')">
                                <i class="fa-solid fa-play"></i>
                            </div>
                        </div>
                        <div class="clip-card-info" style="padding: 0.75rem;">
                            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom: 0.4rem; gap: 0.5rem;">
                                <span style="font-size: 0.7rem; color: var(--ghost); font-family: monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 50%;">${clip.name}</span>
                                ${getPublishBadge(metadata, clip.name)}
                            </div>
                            <div style="margin-bottom: 0.5rem; position: relative; display: flex; gap: 0.4rem;">
                                <input type="text" 
                                       id="title-input-${clip.name}"
                                       value="${titleText}" 
                                       style="background: rgba(255,255,255,0.03); border: 1px solid var(--wire); color: var(--gold); font-size: 0.85rem; font-weight: 600; flex-grow: 1; padding: 6px 8px; border-radius: 4px; transition: all 0.2s;"
                                       onchange="saveClipTitle('${clip.name}', this.value)"
                                       onfocus="this.style.borderColor='var(--gold)'; this.style.background='var(--ink)';"
                                       onblur="this.style.borderColor='var(--wire)'; this.style.background='rgba(255,255,255,0.03)';"
                                       placeholder="Enter viral title #shorts" />
                                <button class="btn btn-mini" onclick="autoGenTitle('${clip.name}', this)" style="padding: 0 8px; border-color: var(--cyan); color: var(--cyan); display: flex; align-items: center; justify-content: center;" title="Auto-Generate Viral Title using Gemini AI"><i class="fa-solid fa-sparkles"></i></button>
                            </div>
                            <div class="clip-meta-row" style="font-size: 0.75rem;">
                                <span>${formatSize(clip.size)}</span>
                                <span>${formatDate(clip.created_at)}</span>
                            </div>
                        </div>
                        ${actionPanel}
                    `;
                    container.appendChild(card);
                });
            } catch (err) {
                container.innerHTML = `<div style="grid-column: span 4; text-align:center; color: var(--ash);">Failed to refresh library: ${err}</div>`;
            }
        }

        async function saveClipTitle(clipName, newTitle) {
            try {
                await fetch('/api/clips/' + encodeURIComponent(clipName) + '/metadata', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title: newTitle })
                });
                console.log('Saved title for', clipName);
            } catch(e) {
                console.error('Failed to save title:', e);
            }
        }

        async function publishClip(clipName) {
            const privacySelect = document.getElementById('privacy-select-' + clipName);
            const privacy = privacySelect ? privacySelect.value : 'private';
            try {
                const res = await fetch('/api/clips/' + encodeURIComponent(clipName) + '/publish?privacy=' + privacy, { method: 'POST' });
                const data = await res.json();
                if (!res.ok) {
                    throw new Error(data.detail || data.message || "Upload failed");
                }
                showToast(data.message, "success");
                // Refresh library to reveal "UPLOADING" badge immediately
                setTimeout(refreshLibrary, 500);
            } catch(e) { showToast('Error publishing clip: ' + (e.message || e), 'error'); }
        }

        async function deleteClip(clipName) {
            if(!confirm('Are you sure you want to delete ' + clipName + '?')) return;
            try {
                const res = await fetch('/api/clips/' + encodeURIComponent(clipName), { method: 'DELETE' });
                if (res.ok) {
                    refreshLibrary();
                    showToast('Clip deleted successfully.', 'success');
                } else {
                    showToast('Delete failed.', 'error');
                }
            } catch(e) { showToast('Error deleting clip: ' + e, 'error'); }
        }

        async function autoGenTitle(clipName, btn) {
            btn.disabled = true;
            const originalHTML = btn.innerHTML;
            btn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i>';
            try {
                const res = await fetch(`/api/clips/${encodeURIComponent(clipName)}/autogen-title`, { method: 'POST' });
                if (res.ok) {
                    const data = await res.json();
                    const input = document.getElementById(`title-input-${clipName}`);
                    if (input) {
                        input.value = data.title;
                    }
                    // Color pop feedback!
                    input.style.borderColor = 'var(--cyan)';
                    setTimeout(() => { input.style.borderColor = 'var(--wire)'; }, 1000);
                    showToast("Title auto-generated successfully!", "success");
                } else {
                    const data = await res.json();
                    showToast("Title Auto-Generation failed: " + (data.detail || "Make sure Gemini API key is configured."), "error");
                }
            } catch(e) {
                showToast("Error generating title: " + e, "error");
            } finally {
                btn.disabled = false;
                btn.innerHTML = originalHTML;
            }
        }

        function getYoutubeId(url) {
            if (!url) return null;
            let match = url.match(/\/shorts\/([a-zA-Z0-9_-]{11})/);
            if (match) return match[1];
            
            const regExp = /^.*(youtu.be\/|v\/|u\/\w\/|embed\/|watch\?v=|\&v=)([^#\&\?]*).*/;
            match = url.match(regExp);
            return (match && match[2].length === 11) ? match[2] : null;
        }

        function openYoutubePreviewModal(name, videoUrl, start, end, index) {
            const videoId = getYoutubeId(videoUrl);
            if (!videoId) {
                showToast("Could not parse YouTube video ID.", "warning");
                return;
            }
            
            const modal = document.getElementById('preview-modal');
            const player = document.getElementById('modal-video-player');
            const ytPlayer = document.getElementById('modal-youtube-player');
            const title = document.getElementById('modal-video-title');
            const download = document.getElementById('modal-video-download');
            
            title.textContent = `${name} (Clip: ${Math.floor(start)}s - ${Math.floor(end)}s)`;
            
            player.style.display = 'none';
            player.src = "";
            
            ytPlayer.style.display = 'block';
            ytPlayer.src = `https://www.youtube.com/embed/${videoId}?start=${Math.floor(start)}&end=${Math.floor(end)}&autoplay=1`;
            
            download.style.display = 'none';
            
            // Read layout dynamic configurations
            let layout = 'crop_center';
            if (index !== undefined) {
                const selectEl = document.getElementById(`crop-layout-select-${index}`);
                if (selectEl) {
                    layout = selectEl.value;
                    if (layout === 'custom_offset') {
                        const sliderEl = document.getElementById(`crop-offset-slider-${index}`);
                        if (sliderEl) {
                            layout = `custom_offset_${sliderEl.value}`;
                        }
                    }
                }
            }
            updateCropPreviewOverlay(layout);
            
            modal.classList.add('active');
        }

        // Video modal controls
        function openVideoModal(name, path) {
            const modal = document.getElementById('preview-modal');
            const player = document.getElementById('modal-video-player');
            const ytPlayer = document.getElementById('modal-youtube-player');
            const title = document.getElementById('modal-video-title');
            const download = document.getElementById('modal-video-download');
            
            title.textContent = name;
            player.src = path;
            player.style.display = 'block';
            
            ytPlayer.src = "";
            ytPlayer.style.display = 'none';
            
            download.href = path;
            download.style.display = 'inline-flex';
            
            // Hide crop overlay mask for finished crop previews
            const overlay = document.getElementById('modal-crop-overlay');
            if (overlay) overlay.style.display = 'none';
            
            modal.classList.add('active');
            player.play();
        }

        function closeVideoModal() {
            const modal = document.getElementById('preview-modal');
            const player = document.getElementById('modal-video-player');
            const ytPlayer = document.getElementById('modal-youtube-player');
            const download = document.getElementById('modal-video-download');
            
            player.pause();
            player.src = "";
            player.style.display = 'block';
            
            ytPlayer.src = "";
            ytPlayer.style.display = 'none';
            
            download.style.display = 'inline-flex';
            
            // Hide the crop overlay mask
            const overlay = document.getElementById('modal-crop-overlay');
            if (overlay) overlay.style.display = 'none';
            
            modal.classList.remove('active');
        }

        // Subtitle & Style Panel helper
        function toggleCustomStylePanel(val) {
            const panel = document.getElementById('custom-style-panel');
            if (val === 'custom') {
                panel.style.display = 'grid';
            } else {
                panel.style.display = 'none';
            }
        }

        // Crop Selection Layout updates
        function updateCropSelection(index) {
            const selectEl = document.getElementById(`crop-layout-select-${index}`);
            const wrapper = document.getElementById(`crop-offset-wrapper-${index}`);
            if (selectEl && wrapper) {
                if (selectEl.value === 'custom_offset') {
                    wrapper.style.display = 'flex';
                } else {
                    wrapper.style.display = 'none';
                }
            }
        }

        function updateCropOffsetLabel(index, val) {
            const label = document.getElementById(`crop-offset-label-${index}`);
            if (label) {
                label.textContent = val + "px";
            }
        }

        function updateCropPreviewOverlay(layout) {
            const overlay = document.getElementById('modal-crop-overlay');
            const leftMask = document.getElementById('crop-mask-left');
            const rightMask = document.getElementById('crop-mask-right');
            const safeBox = document.getElementById('crop-safe-box');
            
            if (!overlay || !leftMask || !rightMask) return;
            
            overlay.style.display = 'flex';
            
            // Reset styles
            leftMask.style.flex = '';
            leftMask.style.width = '';
            leftMask.style.display = 'block';
            rightMask.style.flex = '';
            rightMask.style.width = '';
            rightMask.style.display = 'block';
            
            if (layout === 'crop_left') {
                leftMask.style.display = 'none';
                leftMask.style.width = '0px';
                leftMask.style.flex = 'none';
                rightMask.style.flex = '1';
            } else if (layout === 'crop_right') {
                rightMask.style.display = 'none';
                rightMask.style.width = '0px';
                rightMask.style.flex = 'none';
                leftMask.style.flex = '1';
            } else if (layout.startsWith('custom_offset_')) {
                const offsetVal = parseInt(layout.split('_').pop()) || 0;
                leftMask.style.flex = 'none';
                rightMask.style.flex = 'none';
                
                // Set left and right mask widths relative to center crop (253px safe box width)
                leftMask.style.width = `calc(50% - 126.5px + ${offsetVal}px)`;
                rightMask.style.width = `calc(50% - 126.5px - ${offsetVal}px)`;
            } else {
                // crop_center or auto_track
                leftMask.style.flex = '1';
                rightMask.style.flex = '1';
            }
        }

        // Global Focus listener to track targeted input fields
        document.addEventListener('focusin', (e) => {
            if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) {
                window.lastFocusedInputId = e.target.id;
            }
        });

        // Gemini Metadata Co-Writer Drawer Logic
        function toggleAssistantDrawer(show) {
            const drawer = document.getElementById('assistant-drawer');
            if (!drawer) return;
            if (show) {
                toggleMobileSidebar(false);
                drawer.classList.add('active');
                // Highlight Co-Writer Nav Item
                document.getElementById('nav-item-cowriter').classList.add('active');
            } else {
                drawer.classList.remove('active');
                document.getElementById('nav-item-cowriter').classList.remove('active');
            }
        }

        function addAssistantMessage(sender, text) {
            const chatLog = document.getElementById('assistant-chat-log');
            if (!chatLog) return;
            const msg = document.createElement('div');
            msg.className = `assistant-msg ${sender}`;
            
            if (sender === 'ai') {
                const cleanText = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
                msg.innerHTML = `
                    <div style="margin-bottom: 0.5rem; word-break: break-word;">${cleanText.replace(/\n/g, '<br>')}</div>
                    <button class="assistant-copy-btn" onclick="insertAssistantText(this.parentNode.querySelector('div').innerText)" style="position: static; display: inline-flex; align-items: center; gap: 4px; padding: 2px 6px; border: 1px solid var(--wire); background: var(--ink); border-radius: 4px; font-size: 0.65rem; color: var(--cyan); cursor: pointer;" title="Copy / Paste response">
                        <i class="fa-solid fa-file-export"></i> Insert to Field
                    </button>
                `;
            } else {
                msg.textContent = text;
            }
            
            chatLog.appendChild(msg);
            chatLog.scrollTop = chatLog.scrollHeight;
        }

        async function sendQuickAssistantPrompt(msg) {
            addAssistantMessage('user', msg);
            await queryGeminiAssistant(msg);
        }

        async function sendGeminiAssistantMsg() {
            const input = document.getElementById('assistant-chat-input');
            const prompt = input.value.trim();
            if (!prompt) return;
            
            addAssistantMessage('user', prompt);
            input.value = '';
            await queryGeminiAssistant(prompt);
        }

        async function queryGeminiAssistant(prompt) {
            // Context from current transcript segments
            let context = "(No transcript context loaded)";
            if (window.currentSegments && window.currentSegments.length > 0) {
                context = window.currentSegments.map(s => s.text).join(" ");
            }
            
            // Show typing indicator
            const chatLog = document.getElementById('assistant-chat-log');
            const typing = document.createElement('div');
            typing.className = "assistant-msg ai";
            typing.id = "assistant-typing-indicator";
            typing.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin"></i> Gemini is thinking...`;
            chatLog.appendChild(typing);
            chatLog.scrollTop = chatLog.scrollHeight;
            
            try {
                const res = await fetch('/api/gemini/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        prompt: prompt,
                        context: context
                    })
                });
                
                // Remove typing indicator
                const indicator = document.getElementById('assistant-typing-indicator');
                if (indicator) indicator.remove();
                
                if (res.ok) {
                    const data = await res.json();
                    addAssistantMessage('ai', data.text || "");
                } else {
                    const err = await res.json();
                    addAssistantMessage('ai', "Error from Gemini: " + (err.detail || "Request failed. Check Gemini API key configuration."));
                }
            } catch (err) {
                const indicator = document.getElementById('assistant-typing-indicator');
                if (indicator) indicator.remove();
                addAssistantMessage('ai', "Error connecting to AI: " + err.message);
            }
        }

        async function insertAssistantText(text) {
            const target = document.getElementById('assistant-copy-target').value;
            let targetEl = null;
            
            if (target === 'title') {
                if (window.lastFocusedInputId && window.lastFocusedInputId.startsWith('title-input-')) {
                    targetEl = document.getElementById(window.lastFocusedInputId);
                } else {
                    targetEl = document.querySelector('[id^="title-input-"]');
                }
            }
            
            if (targetEl) {
                targetEl.value = text.trim();
                targetEl.dispatchEvent(new Event('change'));
                targetEl.style.borderColor = 'var(--cyan)';
                setTimeout(() => { targetEl.style.borderColor = 'var(--wire)'; }, 1000);
            } else {
                // Clipboard fallback
                try {
                    await navigator.clipboard.writeText(text);
                    showToast("Text copied to clipboard successfully!", "success");
                } catch (err) {
                    console.error("Clipboard copy failed:", err);
                    showToast("Copy content directly: " + text, "info");
                }
            }
        }

        // Channel Watchdog API Handlers
        async function fetchWatchdog() {
            try {
                const res = await fetch('/api/watchdog');
                const data = await res.json();
                
                document.getElementById('watchdog-toggle').checked = data.enabled || false;
                
                const tbody = document.getElementById('watchdog-channels-list');
                tbody.innerHTML = '';
                
                const channels = data.channels || [];
                if (channels.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--ghost); padding: 1.5rem;">No channels added yet. Watchdog is currently idle.</td></tr>';
                } else {
                    channels.forEach(ch => {
                        const tr = document.createElement('tr');
                        tr.innerHTML = `
                            <td style="font-weight: 600; color: var(--gold);">${ch.name}</td>
                            <td><a href="${ch.url}" target="_blank" style="color: var(--cyan); text-decoration: none;">${ch.url}</a></td>
                            <td style="font-family: monospace; font-size: 0.8rem;">${ch.last_video_id || 'None'}</td>
                            <td style="text-align: right;">
                                <button class="btn btn-mini btn-secondary" onclick="removeWatchdogChannel('${ch.name}')" style="color: #ef4444; border-color: #ef4444; margin: 0;">
                                    <i class="fa-solid fa-trash"></i> Remove
                                </button>
                            </td>
                        `;
                        tbody.appendChild(tr);
                    });
                }
            } catch (err) {
                console.error("Failed to fetch watchdog configuration:", err);
            }
        }

        async function toggleWatchdog(checked) {
            try {
                const resConfig = await fetch('/api/watchdog');
                const config = await resConfig.json();
                
                config.enabled = checked;
                
                const res = await fetch('/api/watchdog', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        enabled: config.enabled,
                        channels: config.channels
                    })
                });
                
                if (res.ok) {
                    console.log("Watchdog polling status set to", checked);
                    showToast("Watchdog polling state updated.", "success");
                } else {
                    showToast("Failed to update watchdog polling state.", "error");
                }
            } catch (err) {
                showToast("Error toggling watchdog: " + err.message, "error");
            }
        }

        async function addWatchdogChannel() {
            const inputEl = document.getElementById('watchdog-url-input');
            const inputVal = inputEl.value.trim();
            if (!inputVal) {
                showToast("Please enter a YouTube channel URL or handle.", "warning");
                return;
            }
            
            let name = inputVal;
            let url = inputVal;
            
            if (inputVal.startsWith('@')) {
                name = inputVal;
                url = `https://www.youtube.com/${inputVal}`;
            } else if (inputVal.includes('youtube.com/')) {
                const match = inputVal.match(/youtube\.com\/(@[a-zA-Z0-9_\-\.]+)/);
                if (match) {
                    name = match[1];
                } else {
                    name = inputVal.split('/').filter(Boolean).pop();
                }
            }
            
            try {
                const resConfig = await fetch('/api/watchdog');
                const config = await resConfig.json();
                
                if (config.channels.some(ch => ch.name.toLowerCase() === name.toLowerCase())) {
                    showToast("This channel is already being monitored.", "warning");
                    return;
                }
                
                config.channels.push({
                    name: name,
                    url: url,
                    last_video_id: ""
                });
                
                const res = await fetch('/api/watchdog', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        enabled: config.enabled,
                        channels: config.channels
                    })
                });
                
                if (res.ok) {
                    inputEl.value = '';
                    fetchWatchdog();
                    showToast(`Channel added to Watchdog successfully.`, "success");
                } else {
                    showToast("Failed to add channel to Watchdog.", "error");
                }
            } catch (err) {
                showToast("Error adding channel: " + err.message, "error");
            }
        }

        async function removeWatchdogChannel(channelName) {
            if (!confirm(`Are you sure you want to remove channel '${channelName}' from the watchdog list?`)) return;
            try {
                const res = await fetch(`/api/watchdog/${encodeURIComponent(channelName)}`, {
                    method: 'DELETE'
                });
                if (res.ok) {
                    fetchWatchdog();
                    showToast(`Channel '${channelName}' removed from Watchdog.`, "success");
                } else {
                    showToast("Failed to remove channel from watchdog.", "error");
                }
            } catch (err) {
                showToast("Error removing channel: " + err.message, "error");
            }
        }

        // App Bootloader
        window.addEventListener('DOMContentLoaded', () => {
            // Restore sidebar state
            if (localStorage.getItem('sidebar-collapsed') === 'true') {
                document.body.classList.add('sidebar-collapsed');
                const icon = document.getElementById('toggle-icon');
                if (icon) icon.className = 'fa-solid fa-angle-right';
            }
            
            // Restore terminal collapsed state
            if (localStorage.getItem('terminal_collapsed') === 'false') {
                toggleTerminalCollapsed(true);
            } else {
                toggleTerminalCollapsed(false);
            }
            
            initLogStream();
            fetchSettings();
            fetchWatchdog();
            checkYouTubeStatus();
            checkInstagramStatus();
            refreshLibrary();
            restoreInteractiveClipperState();
            
            // Auto-fetch video details on paste/input
            const manualUrlInput = document.getElementById('manual-url');
            if (manualUrlInput) {
                manualUrlInput.addEventListener('input', (e) => {
                    const url = e.target.value.trim();
                    if (url.includes('youtube.com/') || url.includes('youtu.be/')) {
                        fetchVideoDetails(url);
                    }
                });
            }
            
            // Check if there is an active running job from a previous session/refresh
            fetch('/api/jobs?status=running')
                .then(res => res.json())
                .then(jobs => {
                    if (jobs && jobs.length > 0) {
                        console.log("Resuming active background job tracking:", jobs[0].id);
                        trackJobProgress(jobs[0].id);
                    }
                }).catch(err => console.error("Error checking active jobs:", err));
            
            // Periodically refresh the stats counts & library status
            setInterval(() => {
                if (typeof refreshLibrary === 'function' && !libraryPollInterval) {
                    refreshLibrary();
                }
                checkYouTubeStatus();
                checkInstagramStatus();
            }, 8000);
        });
    
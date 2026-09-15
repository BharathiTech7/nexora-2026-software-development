const API_BASE_URL = '';

// DOM Elements
const elements = {
    apiStatusBadge: document.getElementById('api-status'),
    statWeeks: document.getElementById('stat-weeks'),
    statPredictions: document.getElementById('stat-predictions'),
    statStatus: document.getElementById('stat-status'),
    weekSelect: document.getElementById('week-select'),
    loadRankingsBtn: document.getElementById('load-rankings-btn'),
    rankingsBody: document.getElementById('rankings-body'),
    gatewayIdInput: document.getElementById('gateway-id'),
    getExplanationBtn: document.getElementById('get-explanation-btn'),
    explanationResult: document.getElementById('explanation-result'),
    runNowBtn: document.getElementById('run-now-btn'),
    runStatusContainer: document.getElementById('run-status-container'),
    runRows: document.getElementById('run-rows'),
    runWeeks: document.getElementById('run-weeks'),
    downloadCsvBtn: document.getElementById('download-csv-btn')
};

// State
let appState = {
    isOnline: false,
    availableWeeks: [],
    isReady: false
};

// Initialize
async function init() {
    await checkHealth();
    setupEventListeners();
}

// Check API Health
async function checkHealth() {
    try {
        const response = await fetch(`${API_BASE_URL}/health`);
        const data = await response.json();
        
        appState.isOnline = true;
        appState.isReady = data.is_ranking_ready;
        appState.availableWeeks = data.available_weeks || [];
        
        updateHealthUI(true);
        updateDashboardStats();
        populateWeekSelect();
        
    } catch (error) {
        console.error('API Health Check Failed:', error);
        appState.isOnline = false;
        updateHealthUI(false);
    }
}

function updateHealthUI(isOnline) {
    elements.apiStatusBadge.className = `status-badge ${isOnline ? 'online' : 'offline'}`;
    elements.apiStatusBadge.innerHTML = `<span class="indicator"></span> ${isOnline ? 'API Online' : 'API Offline'}`;
    
    if (isOnline) {
        elements.statStatus.textContent = appState.isReady ? 'Ready' : 'Data Needed';
        elements.statStatus.className = appState.isReady ? 'text-green' : 'text-red';
    } else {
        elements.statStatus.textContent = 'Offline';
        elements.statStatus.className = 'text-red';
        elements.statWeeks.textContent = '-';
        elements.statPredictions.textContent = '-';
        elements.weekSelect.disabled = true;
        elements.loadRankingsBtn.disabled = true;
        elements.gatewayIdInput.disabled = true;
        elements.getExplanationBtn.disabled = true;
    }
}

function updateDashboardStats() {
    if (appState.isReady && appState.availableWeeks.length > 0) {
        const weeksCount = appState.availableWeeks.length;
        elements.statWeeks.textContent = weeksCount;
        elements.statPredictions.textContent = weeksCount * 15; // 15 gateways per week
    } else {
        elements.statWeeks.textContent = '0';
        elements.statPredictions.textContent = '0';
    }
}

function populateWeekSelect() {
    if (!appState.isReady || appState.availableWeeks.length === 0) {
        elements.weekSelect.innerHTML = '<option value="">No data available (Run required)</option>';
        elements.weekSelect.disabled = true;
        elements.loadRankingsBtn.disabled = true;
        elements.gatewayIdInput.disabled = true;
        elements.getExplanationBtn.disabled = true;
        return;
    }
    
    elements.weekSelect.innerHTML = appState.availableWeeks.map(week => 
        `<option value="${week}">${week}</option>`
    ).join('');
    
    elements.weekSelect.disabled = false;
    elements.loadRankingsBtn.disabled = false;
    elements.gatewayIdInput.disabled = false;
    elements.getExplanationBtn.disabled = false;
}

// Event Listeners
function setupEventListeners() {
    elements.loadRankingsBtn.addEventListener('click', loadRankings);
    elements.getExplanationBtn.addEventListener('click', getExplanation);
    elements.runNowBtn.addEventListener('click', runRankings);
    elements.downloadCsvBtn.addEventListener('click', downloadCsv);
    
    // Allow pressing Enter in gateway ID input
    elements.gatewayIdInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            getExplanation();
        }
    });
}

// Load Rankings
async function loadRankings() {
    const selectedWeek = elements.weekSelect.value;
    if (!selectedWeek) return;
    
    setButtonLoading(elements.loadRankingsBtn, true, 'Loading...');
    
    try {
        const response = await fetch(`${API_BASE_URL}/rankings/${selectedWeek}`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        
        renderRankings(data.gateways);
    } catch (error) {
        console.error('Failed to load rankings:', error);
        elements.rankingsBody.innerHTML = `
            <tr>
                <td colspan="4" class="empty-state">
                    <span class="text-red"><i class="fa-solid fa-triangle-exclamation"></i> Failed to load rankings.</span>
                </td>
            </tr>
        `;
    } finally {
        setButtonLoading(elements.loadRankingsBtn, false, '<i class="fa-solid fa-rotate"></i> Load Rankings');
    }
}

function renderRankings(gateways) {
    if (!gateways || gateways.length === 0) {
        elements.rankingsBody.innerHTML = `
            <tr>
                <td colspan="4" class="empty-state">No rankings found for this week.</td>
            </tr>
        `;
        return;
    }
    
    elements.rankingsBody.innerHTML = gateways.map((gw, index) => {
        const rankClass = index < 3 ? `rank-${index + 1}` : '';
        return `
            <tr class="${rankClass}">
                <td>
                    <span class="rank-badge">${gw.rank}</span>
                </td>
                <td style="font-family: monospace; cursor: pointer;" onclick="document.getElementById('gateway-id').value = '${gw.gateway_id}'; document.getElementById('get-explanation-btn').click();" title="Click to get explanation">
                    ${gw.gateway_id}
                </td>
                <td><strong>${gw.score}</strong></td>
                <td class="text-muted" style="max-width: 300px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${gw.reason}">
                    ${gw.reason}
                </td>
            </tr>
        `;
    }).join('');
}

// Get Explanation
async function getExplanation() {
    const gatewayId = elements.gatewayIdInput.value.trim();
    const selectedWeek = elements.weekSelect.value;
    
    if (!gatewayId || !selectedWeek) {
        showExplanationInfo('Please select a week and enter a Gateway ID.', 'info');
        return;
    }
    
    setButtonLoading(elements.getExplanationBtn, true, 'Getting...');
    
    try {
        const response = await fetch(`${API_BASE_URL}/gateways/${gatewayId}/explanation?week=${selectedWeek}`);
        const data = await response.json();
        
        if (!response.ok) {
            showExplanationInfo(data.detail || 'Gateway not found or error occurred.', 'error');
            return;
        }
        
        showExplanationResult(data);
    } catch (error) {
        console.error('Failed to get explanation:', error);
        showExplanationInfo('Network error while getting explanation.', 'error');
    } finally {
        setButtonLoading(elements.getExplanationBtn, false, '<i class="fa-solid fa-wand-magic-sparkles"></i> Get Explanation');
    }
}

function showExplanationInfo(message, type = 'info') {
    let icon = 'fa-circle-info';
    if (type === 'error') icon = 'fa-circle-xmark';
    if (type === 'success') icon = 'fa-circle-check';
    
    elements.explanationResult.className = `info-box ${type}`;
    elements.explanationResult.innerHTML = `
        <i class="fa-solid ${icon}"></i>
        <p>${message}</p>
    `;
}

function showExplanationResult(data) {
    elements.explanationResult.className = 'info-box success';
    elements.explanationResult.innerHTML = `
        <i class="fa-solid fa-circle-check"></i>
        <div style="flex:1;">
            <p style="margin-bottom: 0.5rem;"><strong>Gateway ID:</strong> ${data.gateway_id} | <strong>Rank:</strong> ${data.rank} | <strong>Score:</strong> ${data.score}</p>
            <p style="color: var(--text-main); font-size: 0.95rem;">${data.reason}</p>
        </div>
    `;
}

// Run Rankings
async function runRankings() {
    setButtonLoading(elements.runNowBtn, true, '<i class="fa-solid fa-spinner spin"></i> Running Computation...');
    elements.runStatusContainer.classList.add('hidden');
    
    try {
        const response = await fetch(`${API_BASE_URL}/run`, { method: 'POST' });
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to compute rankings');
        }
        
        // Success
        elements.runStatusContainer.classList.remove('hidden');
        elements.runRows.textContent = data.total_rows;
        elements.runWeeks.textContent = data.weeks_count;
        
        // Refresh health to get updated weeks
        await checkHealth();
        
    } catch (error) {
        console.error('Run failed:', error);
        alert(`Error running rankings: ${error.message}`);
    } finally {
        setButtonLoading(elements.runNowBtn, false, '<i class="fa-solid fa-play"></i> Run Now');
    }
}

// Download CSV
function downloadCsv() {
    window.location.href = '/predictions.csv';
}

// Utilities
function setButtonLoading(button, isLoading, html) {
    if (isLoading) {
        button.disabled = true;
        button.dataset.originalHtml = button.innerHTML;
        button.innerHTML = html;
    } else {
        button.disabled = false;
        button.innerHTML = html;
    }
}

// Run init
init();

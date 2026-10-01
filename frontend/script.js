const API_BASE = "http://localhost:8000";
let currentJobId = null;
let pollInterval = null;

// Elements
const submitForm = document.getElementById("submit-form");
const topicInput = document.getElementById("topic-input");
const estimateResult = document.getElementById("estimate-result");
const approveBtn = document.getElementById("approve-btn");
const statusSection = document.getElementById("status-section");
const submitSection = document.getElementById("submit-section");
const downloadSection = document.getElementById("download-section");
const jobStatus = document.getElementById("job-status");
const jobStage = document.getElementById("job-stage");
const jobProgress = document.getElementById("job-progress");
const jobIdSpan = document.getElementById("job-id");
const errorMsg = document.getElementById("error-msg");
const errorText = document.getElementById("error-text");
const downloadBtn = document.getElementById("download-btn");
const regenerateBtn = document.getElementById("regenerate-btn");
const newJobBtn = document.getElementById("new-job-btn");

// Submit form
submitForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const topic = topicInput.value.trim();
    if (!topic) return;

    try {
        const res = await fetch(`${API_BASE}/job/submit`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ topic })
        });
        const data = await res.json();

        currentJobId = data.job_id;
        document.getElementById("est-images").textContent = data.estimated_images;
        document.getElementById("est-cost").textContent = `$${data.estimated_cost_usd.toFixed(2)}`;
        document.getElementById("est-time").textContent = data.estimated_duration_min;

        estimateResult.classList.remove("hidden");
    } catch (err) {
        alert(`Error: ${err.message}`);
    }
});

// Approve
approveBtn.addEventListener("click", async () => {
    try {
        await fetch(`${API_BASE}/job/${currentJobId}/approve`, {
            method: "POST"
        });

        submitSection.classList.add("hidden");
        statusSection.classList.remove("hidden");
        jobIdSpan.textContent = currentJobId;

        startPolling();
    } catch (err) {
        alert(`Error: ${err.message}`);
    }
});

// Poll status
async function startPolling() {
    if (pollInterval) clearInterval(pollInterval);
    
    pollInterval = setInterval(async () => {
        try {
            const res = await fetch(`${API_BASE}/job/${currentJobId}`);
            const data = await res.json();

            jobStatus.textContent = data.status;
            jobStage.textContent = data.stage || "-";
            jobProgress.textContent = data.progress || "-";

            if (data.error_msg) {
                errorMsg.classList.remove("hidden");
                errorText.textContent = data.error_msg;
            }

            if (data.status === "done" || data.status === "finalized") {
                clearInterval(pollInterval);
                showDownload();
            }
        } catch (err) {
            console.error("Poll error:", err);
        }
    }, 2000);
}

// Show download
function showDownload() {
    statusSection.classList.add("hidden");
    downloadSection.classList.remove("hidden");
    downloadBtn.href = `${API_BASE}/job/${currentJobId}/video`;
}

// Regenerate
regenerateBtn.addEventListener("click", async () => {
    try {
        await fetch(`${API_BASE}/job/${currentJobId}/regenerate`, {
            method: "POST"
        });

        downloadSection.classList.add("hidden");
        statusSection.classList.remove("hidden");
        errorMsg.classList.add("hidden");

        startPolling();
    } catch (err) {
        alert(`Error: ${err.message}`);
    }
});

// New job
newJobBtn.addEventListener("click", () => {
    location.reload();
});

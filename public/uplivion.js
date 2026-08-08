"use strict";

// --- Global variables ---

let burgerIcon;
let menuItems;
let profileBtn;
let logoutBtn;
let container;
let dropArea;
let fileInput;
let fileName;
let fileSize;
let progressBar;
let progressBarText;
let dropAreaMessage;
let dropAreaMessageText;
let uploadMaxDownloads;
let uploadExpiryValue;
let uploadExpiryUnit;
let uploadBtn;
let cancelBtn;
let pauseBtn;
let resumeBtn;
let copyBtn;
let showLinksBtn;
let linksOverlay;
let linksModal;
let linksList;
let quotaMessage;
let adminBtn;

// The signed-in user, from /check (identity + role).
let currentUser = null;

// Admin panel selection state
let adminUsersCache = [];
let adminSelectedId = null;

let selectedFile = null;
let uploadedLink = null;
let currentLinks = [];
let linksSelectedId = null;   // file_id of the selected file-manager card
let isPaused = false;
let activeUploadID = null;

let access_token = null;
let refreshPromise = null;

const PASSWORD_MAX_BYTES = 72;



// --- Helper Functions ---

function passwordByteLength(value) {
    return new TextEncoder().encode(value).length;
}

// Fix mobile vh bug
function setVhUnit() {
    document.documentElement.style.setProperty('--vh', `${window.innerHeight * 0.01}px`);
}
window.addEventListener('resize', setVhUnit);
window.addEventListener('orientationchange', setVhUnit);
setVhUnit();

function formatFileSize(bytes) {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    let size = bytes;
    while (size >= 1024 && i < units.length - 1) {
        size /= 1024;
        i++;
    }

    // No decimals for bytes, two decimals otherwise
    if (i === 0) {
        return `${size} ${units[i]}`;
    } else {
        return `${size.toFixed(2)} ${units[i]}`;
    }
}

// Helper to format bytes/sec into human readable string
function formatSpeed(bytesPerSec) {
    if (bytesPerSec < 1024) return bytesPerSec.toFixed(0) + " B/s";
    else if (bytesPerSec < 1024 * 1024) return (bytesPerSec / 1024).toFixed(1) + " KB/s";
    else return (bytesPerSec / (1024 * 1024)).toFixed(1) + " MB/s";
}

// Helper to format seconds into mm:ss
function formatTime(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    if (mins > 0) {
        return `${mins}m ${secs}s`;
    } else {
        return `${secs}s`;
    }
}

function getExpirySeconds(expiryValue, expiryUnit) {
    let val = parseInt(expiryValue.value, 10);
    if (isNaN(val) || val <= 0) return 0; // invalid

    switch (expiryUnit.value.toLowerCase()) {
    case "seconds":
    case "s":
        return val;

    case "minutes":
    case "m":
        return val * 60;

    case "hours":
    case "h":
        return val * 3600;

    case "days":
    case "d":
        return val * 86400;

    default:
        return val;
    }
}

function formatExpiry(seconds, revoked) {
    if (revoked) return "Revoked";
    if (seconds <= 0) return "Expired";

    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;

    let parts = [];
    if (days) parts.push(`${days}d`);
    if (hours) parts.push(`${hours}h`);
    if (minutes) parts.push(`${minutes}m`);
    if (secs && !days && !hours) parts.push(`${secs}s`); // only show seconds if short time

    // No "Expiry:" prefix — callers pair this with a clock icon / a label.
    return parts.join(' ');
}

// The absolute moment a link expires, from its remaining seconds. Revoked links
// have no meaningful expiry time.
function formatExpiryDate(secondsFromNow, revoked) {
    if (revoked) return "—";
    const d = new Date(Date.now() + (Number(secondsFromNow) || 0) * 1000);
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function showMessage(msg, target, duration = 2000, stateClass = "") {
    if (!target) return;

    target.textContent = msg;

    // Remove previous state classes
    target.classList.remove("copied", "revoked", "error", "regenerated", "paused");

    // Add new state class if provided
    if (stateClass) target.classList.add(stateClass);

    // Show the message
    target.style.opacity = "1";
    target.style.transition = "opacity 0.5s ease";

    // Hide after duration
    if (duration > 0) {
        setTimeout(() => {
            target.style.opacity = "0";
        }, duration);
    }
}



// --- Reset functions ---

function enableDropAreaAndInput() {
    dropArea.addEventListener("dragover", dropAreaDragOver);
    dropArea.addEventListener("dragleave", dropAreaDragLeave);
    dropArea.addEventListener("drop", dropAreaDrop);
    dropArea.addEventListener("click", dropAreaClick);
    dropArea.classList.remove("inactive");
    fileInput.addEventListener("change", dropAreaFileChange);
}

function disableDropAreaAndInput() {
    dropArea.removeEventListener("dragover", dropAreaDragOver);
    dropArea.removeEventListener("dragleave", dropAreaDragLeave);
    dropArea.removeEventListener("drop", dropAreaDrop);
    dropArea.removeEventListener("click", dropAreaClick);
    dropArea.classList.add("inactive");
    fileInput.removeEventListener("change", dropAreaFileChange);
}

function resetUI() {
    uploadBtn.disabled = true;
    copyBtn.disabled = true;
    selectedFile = null;
    uploadedLink = null;
    fileInput.value = "";
    fileSize.textContent = "";
    fileName.textContent = "Drag & Drop files here or click to browse";
    uploadMaxDownloads.value = "";
    uploadExpiryValue.value = "";
    uploadExpiryUnit.value = "days";
    dropAreaMessageText.textContent = "";
    dropAreaMessageText.style.color = "";
    progressBar.style.width = "0%";
    progressBarText.textContent = "";
    uploadMaxDownloads.disabled = false;
    uploadExpiryValue.disabled = false;
    uploadExpiryUnit.disabled = false;
    enableDropAreaAndInput();
}



// --- Uplivion main UI loader functions ---

function initializeElements(wrapper) {
    burgerIcon = wrapper.querySelector("#burger-icon");
    menuItems = wrapper.querySelector("#menu-items");
    profileBtn = wrapper.querySelector("#profile-btn");
    logoutBtn = wrapper.querySelector("#logout");
    container = wrapper.querySelector(".container");
    dropArea = wrapper.querySelector("#drop-area");
    fileInput = wrapper.querySelector("#upload");
    fileName = wrapper.querySelector("#file-name");
    fileSize = wrapper.querySelector("#file-size");
    progressBar = dropArea.querySelector(".progress-bar");
    progressBarText = dropArea.querySelector(".progress-text");
    dropAreaMessage = wrapper.querySelector("#drop-area-message");
    dropAreaMessageText = dropAreaMessage.querySelector(".drop-area-message-text");
    uploadMaxDownloads = wrapper.querySelector("#max-downloads-value");
    uploadExpiryValue = wrapper.querySelector("#expiry-value");
    uploadExpiryUnit = wrapper.querySelector("#expiry-unit");
    uploadBtn = wrapper.querySelector("#upload-btn");
    cancelBtn = wrapper.querySelector("#cancel-btn");
    pauseBtn = wrapper.querySelector("#pause-btn");
    resumeBtn = wrapper.querySelector("#resume-btn");
    copyBtn = wrapper.querySelector("#copy-btn");
    showLinksBtn = wrapper.querySelector("#show-links-btn");
    linksOverlay = wrapper.querySelector("#links-overlay");
    linksModal = wrapper.querySelector("#links-modal");
    linksList = wrapper.querySelector("#links-list");
    quotaMessage = wrapper.querySelector("#quota-display");
    adminBtn = wrapper.querySelector("#admin-btn");
}

function attachEventListeners() {
    // Burger menu listeners
    burgerIcon.addEventListener("click", () => {
        menuItems.classList.toggle("show");
        burgerIcon.classList.toggle("open");
        burgerIcon.setAttribute(
            "aria-expanded",
            String(menuItems.classList.contains("show"))
        );
    });

    // Hide menu if clicking outside
    document.addEventListener("click", (e) => {
        if (!burgerIcon.contains(e.target) && !menuItems.contains(e.target)) {
            menuItems.classList.remove("show");
            burgerIcon.classList.remove("open");
            burgerIcon.setAttribute("aria-expanded", "false");
        }
    });

    // Admin panel listener
    adminBtn.addEventListener("click", openAdminPanel);

    // Profile listener (name, password, delete files / account)
    profileBtn.addEventListener("click", openProfileOverlay);

    // Logout listener
    logoutBtn.addEventListener("click", logout);

    // Drag & Drop listeners
    dropArea.addEventListener("dragover", dropAreaDragOver);
    dropArea.addEventListener("dragleave", dropAreaDragLeave);
    dropArea.addEventListener("drop", dropAreaDrop);

    // Click to browse listeners
    dropArea.addEventListener("click", dropAreaClick);
    fileInput.addEventListener("change", dropAreaFileChange);

    // Expiry input listener
    uploadExpiryValue.addEventListener("input", (e) => {
        // Keep only digits, max 5 characters, and enable or disable the upload button
        uploadExpiryValue.value = uploadExpiryValue.value.replace(/\D/g, "").slice(0, 5);

        // Update the state of the upload button
        validateUpload()
    });

    // Max-downloads input listener: digits only, and it gates the upload button
    // (both limits must be set before uploading).
    uploadMaxDownloads.addEventListener("input", () => {
        uploadMaxDownloads.value = uploadMaxDownloads.value.replace(/\D/g, "").slice(0, 9);
        validateUpload();
    });

    // Upload button listener
    uploadBtn.addEventListener("click", uploadButton);

    // Copy button listener
    copyBtn.addEventListener("click", copyButton);

    // Show links modal listener
    showLinksBtn.addEventListener("click", showLinksButton);

    // Click outside links modal closes it
    linksOverlay.addEventListener("click", (e) => {
        if (e.target === linksOverlay) {
            closeLinksModal();
        }
    });

    // Handle back/forward buttons for modal
    window.addEventListener("popstate", (event) => {
        if (event.state && event.state.modal) {
            linksOverlay.classList.add("active");
            document.body.classList.add("modal-open");
        } else {
            closeLinkSettings();
            linksSelectedId = null;
            linksOverlay.classList.remove("active");
            document.body.classList.remove("modal-open");
        }
    });

    // Re-fit the card names when the viewport width changes.
    window.addEventListener("resize", () => {
        if (linksOverlay.classList.contains("active")) applyCardNameEllipsis();
    });

    // ESC closes the settings overlay first, then the file manager.
    document.addEventListener("keydown", (e) => {
        if (e.key !== "Escape") return;
        const settingsOverlay = document.querySelector("#links-settings-overlay");
        if (settingsOverlay && !settingsOverlay.classList.contains("hidden")) {
            closeLinkSettings();
        } else if (linksOverlay.classList.contains("active")) {
            closeLinksModal();
        }
    });
}



// --- Service unavailable overlay ---

function showServiceOverlay(show) {
    const existing = document.getElementById("service-unavailable");

    if (show) {
        if (existing) return; // already there

        const overlay = document.createElement("div");
        overlay.id = "service-unavailable";
        overlay.textContent = "Service Unavailable";

        let wrapper = document.querySelector(".container-wrapper");

        // If wrapper doesn't exist, create it
        if (!wrapper) {
            wrapper = document.createElement("div");
            wrapper.className = "container-wrapper";

            // Add #main-title at the top
            const title = document.createElement("div");
            title.id = "main-title";
            title.textContent = "Uplivion";
            wrapper.appendChild(title);

            document.body.appendChild(wrapper);
        } else {
            // Ensure #main-title exists at the top
            if (!wrapper.querySelector("#main-title")) {
                const title = document.createElement("div");
                title.id = "main-title";
                title.textContent = "Uplivion";
                wrapper.insertBefore(title, wrapper.firstChild);
            }

            // Remove everything else inside wrapper except the #main-title
            [...wrapper.children].forEach(child => {
                if (child.id !== "main-title") {
                    child.remove();
                }
            });
        }

        // Add the overlay after the title
        wrapper.appendChild(overlay);

    } else {
        if (existing) existing.remove();
    }
}

// --- Rate limit overlay ---

function showRateLimitOverlay(show, seconds) {
    const existing = document.getElementById("rate-limit");

    if (show) {
        if (existing) return; // already there

        // Remove existing login/app wrapper
        const wrapper = document.querySelector(".container-wrapper");
        if (wrapper) wrapper.remove();

        // Create new wrapper
        const newWrapper = document.createElement("div");
        newWrapper.className = "container-wrapper";

        // Optional title
        const title = document.createElement("div");
        title.id = "main-title";
        title.textContent = "Uplivion";
        newWrapper.appendChild(title);

        // Overlay
        const overlay = document.createElement("div");
        overlay.id = "rate-limit";
        overlay.className = "rate-limit-overlay";

        // Main message
        const message = document.createElement("div");
        message.textContent = "Slow down, Cowboy!";
        overlay.appendChild(message);

        // Timer message
        const timerMsg = document.createElement("div");
        overlay.appendChild(timerMsg);

        newWrapper.appendChild(overlay);
        document.body.appendChild(newWrapper);

        // Countdown timer
        let countdown = seconds;
        timerMsg.textContent = `Taking you home in ${countdown}s...`;

        const interval = setInterval(() => {
            countdown--;
            if (countdown > 0) {
                timerMsg.textContent = `Taking you home in ${countdown}s...`;
            } else if (countdown === 0) {
                timerMsg.textContent = `Going home now...`;
            } else {
                clearInterval(interval);
            }
        }, 1000);

        // Redirect after specified seconds
        setTimeout(() => {
            showLoginPage();
        }, (seconds + 2) * 1000);

    } else {
        // Remove overlay if exists
        if (existing) existing.remove();
    }
}



// --- Busy overlay ---

function busyOverlay(message = "Processing...", delay = 1000) {
    // Create overlay if it doesn't exist
    let busy_overlay = document.getElementById("busy-overlay");
    if (!busy_overlay) {
        busy_overlay = document.createElement("div");
        busy_overlay.id = "busy-overlay";
        busy_overlay.className = "busy-overlay"; // keep your CSS class
        busy_overlay.style.display = "none";     // start hidden

        // Spinner div
        const spinner = document.createElement("div");
        spinner.className = "spinner";

        // Text div (safe because we use textContent)
        const textDiv = document.createElement("div");
        textDiv.className = "busy-overlay-text";
        textDiv.textContent = message;

        // Append children
        busy_overlay.appendChild(spinner);
        busy_overlay.appendChild(textDiv);

        document.body.appendChild(busy_overlay);
    }

    // Start a timer to show the overlay after `delay` ms
    let timeoutId = setTimeout(() => {
        const busy_overlay = document.getElementById("busy-overlay");
        busy_overlay.querySelector(".busy-overlay-text").textContent = message;
        busy_overlay.style.display = "flex";
    }, delay);

    return {
        // Cancel showing overlay if operation finishes quickly
        cancel: () => clearTimeout(timeoutId),
        // Show overlay immediately
        immediate: () => {
            clearTimeout(timeoutId);
            const busy_overlay = document.getElementById("busy-overlay");
            busy_overlay.querySelector(".busy-overlay-text").textContent = message;
            busy_overlay.style.display = "flex";
        },
        // Hide overlay
        hide: () => {
            clearTimeout(timeoutId);
            const busy_overlay = document.getElementById("busy-overlay");
            busy_overlay.style.display = "none";
        }
    };
}



// --- Overwrite overlay ---

function overwriteOverlay(filename, sizeBytes, newSizeBytes, uploaded, hash) {
    return new Promise((resolve, reject) => {
        // Convert size to human-readable
        const sizeFormatted = formatFileSize(sizeBytes);
        const newSizeFormatted = formatFileSize(newSizeBytes);

        // Overlay
        const overlay = document.createElement("div");
        overlay.id = "overwrite-overlay";
        overlay.className = "overwrite-overlay active";

        // Modal
        const modal = document.createElement("div");
        modal.id = "overwrite-modal";

        // Title
        const title = document.createElement("h2");
        title.textContent = "File already exists. Overwrite?";

        // File details
        const details = document.createElement("div");
        details.className = "file-details";

        function addDetailRow(...parts) {
            const row = document.createElement("div");
            for (const part of parts) {
                if (part.label) {
                    const strong = document.createElement("strong");
                    strong.textContent = part.label;
                    row.appendChild(strong);
                } else {
                    row.appendChild(document.createTextNode(part.text));
                }
            }
            details.appendChild(row);
        }

        addDetailRow({ label: "Name:" }, { text: ` ${filename}` });
        addDetailRow({ label: "Uploaded:" }, { text: ` ${uploaded}` });
        addDetailRow({ label: "Old size:" }, { text: ` ${sizeFormatted} | ` }, { label: "New size:" }, { text: ` ${newSizeFormatted}` });
        addDetailRow({ label: "SHA-256:" }, { text: ` ${hash}` });
        addDetailRow();
        addDetailRow({ label: "Note:" }, { text: " This will delete the existing file on the server immediately." });

        // Buttons
        const buttonsDiv = document.createElement("div");
        buttonsDiv.className = "button-row";

        const okBtn = document.createElement("button");
        okBtn.className = "overwrite-ok";
        okBtn.textContent = "Yes";

        const cancelBtn = document.createElement("button");
        cancelBtn.className = "overwrite-cancel";
        cancelBtn.textContent = "No";

        buttonsDiv.appendChild(okBtn);
        buttonsDiv.appendChild(cancelBtn);

        // Assemble
        modal.appendChild(title);
        modal.appendChild(details);
        modal.appendChild(buttonsDiv);
        overlay.appendChild(modal);
        document.body.appendChild(overlay);

        // Handlers
        okBtn.addEventListener("click", () => {
            cleanup();
            resolve(true);
        });
        cancelBtn.addEventListener("click", () => {
            cleanup();
            reject(new DOMException("Overwrite cancelled", "AbortError"));
        });
        overlay.addEventListener("click", (e) => {
            if (e.target === overlay) {
                cleanup();
                reject(new DOMException("Overwrite cancelled", "AbortError"));
            }
        });

        function cleanup() {
            document.body.removeChild(overlay);
        }
    });
}



// --- Password change overlay ---

function changePwdOverlay() {
    return new Promise((resolve) => {
        const overlay = document.getElementById("change-password-overlay");
        const oldInput = document.getElementById("old-password");
        const newInput = document.getElementById("new-password");
        const repeatInput = document.getElementById("repeat-password");
        const msg = document.getElementById("change-password-msg");
        const okBtn = overlay.querySelector(".change-password-ok");
        const cancelBtn = overlay.querySelector(".change-password-cancel");

        overlay.classList.add("active");
        oldInput.value = "";
        newInput.value = "";
        repeatInput.value = "";
        msg.textContent = "";

        // Remove previous eye toggle listeners
        overlay.querySelectorAll(".eye-container").forEach(container => {
            const clone = container.cloneNode(true); // clone removes all previous listeners
            container.parentNode.replaceChild(clone, container);

            const input = clone.previousElementSibling;
            const eyeClosed = clone.querySelector(".eye-closed");
            const eyeOpen = clone.querySelector(".eye-open");
            const fieldLabel = clone.dataset.label || "password";
            clone.addEventListener("click", () => {
                if (input.type === "password") {
                    input.type = "text";
                    clone.setAttribute("aria-label", `Hide ${fieldLabel}`);
                    eyeClosed.classList.add("hidden");
                    eyeOpen.classList.remove("hidden");
                } else {
                    input.type = "password";
                    clone.setAttribute("aria-label", `Show ${fieldLabel}`);
                    eyeOpen.classList.add("hidden");
                    eyeClosed.classList.remove("hidden");
                }
            });
        });

        // OK button
        const okHandler = async () => {
            msg.textContent = "";

            if ([oldInput, newInput, repeatInput].some(
                input => passwordByteLength(input.value) > PASSWORD_MAX_BYTES
            )) {
                showMessage("Passwords cannot exceed 72 UTF-8 bytes", msg, 2000, "error");
                return;
            }

            try {
                const res = await fetchProtected("/changepwd", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        old_password: oldInput.value,
                        new_password: newInput.value,
                        repeat_password: repeatInput.value
                    })
                });

                showMessage(res.message, msg, 2000, "copied");
                setTimeout(() => {
                    overlay.classList.remove("active");
                    oldInput.value = "";
                    newInput.value = "";
                    repeatInput.value = "";
                    resolve(true);
                }, 2500);
            } catch (err) {
                showMessage(
                    err.message || "Failed to change password",
                    msg,
                    2000,
                    "error"
                );
                console.error(err);
            }
        };
        okBtn.onclick = okHandler;

        // Cancel button
        const cancelHandler = () => {
            overlay.classList.remove("active");
            oldInput.value = "";
            newInput.value = "";
            repeatInput.value = "";
            resolve(false);
        };
        cancelBtn.onclick = cancelHandler;

        // Click outside overlay
        const overlayHandler = (e) => {
            if (e.target === overlay) {
                oldInput.value = "";
                newInput.value = "";
                repeatInput.value = "";
                overlay.classList.remove("active");
                resolve(false);
            }
        };
        overlay.onclick = overlayHandler;
    });
}


// Profile overlay: edit name, open the change-password overlay, delete all own
// files, or delete the account. Closes on a backdrop click.
function openProfileOverlay() {
    const overlay = document.getElementById("profile-overlay");
    const firstInput = document.getElementById("profile-first-name");
    const lastInput = document.getElementById("profile-last-name");
    const msg = document.getElementById("profile-msg");
    const saveBtn = document.getElementById("profile-save");
    const changePwdBtn = document.getElementById("profile-change-pwd");
    const deleteFilesBtn = document.getElementById("profile-delete-files");
    const deleteAccountBtn = document.getElementById("profile-delete-account");

    firstInput.value = currentUser ? currentUser.first_name : "";
    lastInput.value = currentUser ? currentUser.last_name : "";
    msg.textContent = "";
    msg.style.opacity = "1";

    overlay.classList.add("active");

    const close = () => overlay.classList.remove("active");
    overlay.onclick = (e) => {
        if (e.target === overlay) close();
    };

    saveBtn.onclick = async () => {
        try {
            const res = await fetchProtected("/profile", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    first_name: firstInput.value,
                    last_name: lastInput.value,
                }),
            });
            if (!res) return;
            if (currentUser) {
                currentUser.first_name = res.first_name;
                currentUser.last_name = res.last_name;
            }
            showMessage("Profile updated", msg, 2000, "copied");
        } catch (err) {
            showMessage(err.message || "Failed to update profile", msg, 2000, "error");
        }
    };

    // The change-password overlay is a standalone modal reached from here.
    changePwdBtn.onclick = () => {
        close();
        changePwdOverlay();
    };

    deleteFilesBtn.onclick = async () => {
        const ok = await confirmDialog(
            "Delete ALL your files? This cannot be undone. Your account is kept.",
            "Delete files",
        );
        if (!ok) return;
        try {
            const res = await fetchProtected("/profile/files", {method: "DELETE"});
            if (!res) return;
            showMessage(
                `Deleted ${res.deleted} file${res.deleted === 1 ? "" : "s"}`,
                msg, 2000, "copied",
            );
            updateQuota();
            updateLinksButtonState();
        } catch (err) {
            showMessage(err.message || "Failed to delete files", msg, 2000, "error");
        }
    };

    deleteAccountBtn.onclick = async () => {
        const ok = await confirmDialog(
            "Delete your account and all your files? This cannot be undone.",
            "Delete account",
        );
        if (!ok) return;
        try {
            const res = await fetchProtected("/profile", {method: "DELETE"});
            if (!res) return;
            access_token = null;
            close();
            showLoginPage();
        } catch (err) {
            showMessage(err.message || "Failed to delete account", msg, 2000, "error");
        }
    };
}


/**
 * Fetches a new access token from the backend session endpoint.
 *
 * Returns:
 * - The access token string if successful.
 * - The HTTP status code if the request completed but returned an error.
 *
 * Network-level errors (server unreachable, offline, DNS error, CORS failure, etc.)
 * will cause this function to throw, allowing the caller to handle it.
 *
 *
 * Note about fetch() and Response:
 * - fetch() always returns a Promise that resolves to a Response object.
 * - The Response object represents the HTTP response from the backend and contains:
 *     - res.ok         → true if status is 200–299, false otherwise
 *     - res.status     → numeric HTTP status code (e.g., 200, 401, 404, 500)
 *     - res.statusText → status message from server ("OK", "Unauthorized", etc.)
 *     - res.headers    → Headers object
 *     - res.url        → URL of the response
 *     - res.type       → Response type ("basic", "cors", etc.)
 *     - res.body       → ReadableStream of the response body (raw response data)
 * - Body-parsing methods let you extract the response in different formats:
 *     - res.json()        → parses the response body as JSON (common for APIs)
 *     - res.text()        → reads the body as plain text
 *     - res.blob()        → reads the body as Blob (binary)
 *     - res.arrayBuffer() → reads the body as ArrayBuffer
 *     - res.formData()    → reads the body as FormData
 * - Important behavior:
 *     1. HTTP errors (e.g., 404, 500) do NOT reject the fetch promise.
 *        Use res.ok or res.status to detect them.
 *     2. Network-level errors (server unreachable, offline, DNS failure, CORS failure)
 *        DO reject the fetch promise and must be caught by the caller.
 *     3. The response body may contain JSON from the backend (e.g., access tokens, error messages),
 *        which should be parsed safely using res.json().
 *
 *
 * THROW VS RETURN IN JAVASCRIPT
 *
 * 1. Stopping execution:
 *    - The only way to automatically stop the execution of caller code is to `throw` an error.
 *    - `return` only stops the current function; it does NOT stop the caller from continuing.
 *
 * 2. Regular (synchronous) functions:
 *    - `throw` stops the current function immediately.
 *    - The error propagates up the call stack until caught by a `try/catch`.
 *      Example:
 *        function foo() { throw new Error("stop"); }
 *        try { foo(); console.log("won't run"); } catch(e) { console.log(e.message); }
 *
 * 3. Async functions:
 *    - `throw` stops the async function immediately and causes the returned Promise to reject.
 *    - The caller using `await` will stop executing unless the error is caught with `try/catch`.
 *      Example:
 *        async function asyncFunc() { throw new Error("async error"); }
 *        async function main() { await asyncFunc(); console.log("won't run"); }
 *
 * 4. Promises:
 *    - `throw` inside a Promise executor function rejects the Promise.
 *    - Rejected Promises can be handled with `.catch()` or try/catch with `await`.
 *
 * 5. Return vs throw:
 *    - `return` stops only the current function.
 *    - `throw` stops the function and propagates up the call stack (sync or async).
 *
 * Summary:
 *    - Use `throw` to halt execution in the caller.
 *    - Use `return` to exit only the current function while allowing the caller to continue.
 */
async function parseResponseError(res) {
    let message = `Request failed with code ${res.status}`;
    try {
        const data = await res.json();
        if (data && data.error) message = data.error;
    } catch {
        // The stable status fallback is used for non-JSON error pages.
    }
    const error = new Error(message);
    error.status = res.status;
    return error;
}

async function refreshAccessToken() {
    const res = await fetch("/session", {
        method: "POST",
        credentials: "include"
    });
    if (!res.ok) throw await parseResponseError(res);

    const data = await res.json();
    access_token = data.access_token;
    return access_token;
}

async function getAccessToken() {
    if (!refreshPromise) {
        refreshPromise = refreshAccessToken().finally(() => {
            refreshPromise = null;
        });
    }
    return refreshPromise;
}


/**
 * Fetch a protected backend endpoint using the current access token.
 * Handles automatic token refresh and error propagation.
 *
 * Behavior and error handling:
 *
 * 1. Network-level errors:
 *    - Occur when the fetch request cannot be completed at all.
 *      Examples: server completely down, network unplugged, user offline, DNS resolution failure,
 *      or CORS/preflight check failure.
 *    - In these cases, the browser cannot establish a connection to a server,
 *      so `fetch` does **not** return a Response object. Instead, it immediately throws a rejected promise.
 *    - This is different from an HTTP error response (!res.ok), where the server was reached
 *      and returned a valid HTTP response with a non-2xx status code.
 *    - These network-level errors are caught by a try/catch block and can be rethrown
 *      (e.g., as `new Error("${url} fetching failed")`) or handled appropriately, propagating to the caller.
 *
 * 2. HTTP responses (res.ok === false):
 *    - Indicates that the request successfully reached a server and the server returned a valid HTTP response,
 *      but the status code is outside the 200–299 range (e.g., 401, 403, 500).
 *    - If status is 401, the function attempts to refresh the token via getAccessToken().
 *      If refresh fails, an "Unauthorized" Error is thrown so the caller can redirect to login.
 *    - For other non-2xx statuses, the function attempts to parse the response as JSON to extract an error message.
 *      If parsing succeeds, an Error with that message is thrown; otherwise, a generic Error including the HTTP status is thrown.
 *
 * 3. JSON parsing errors:
 *    - If parsing the response body as JSON fails, an Error is thrown with a generic message including the status code.
 *
 * 4. Propagation:
 *    - All Errors propagate to the caller of fetchProtected().
 *    - Callers can handle them with try/catch to display overlays, redirect to login, or log messages.
 */
async function fetchProtectedResponse(url, options = {}) {
    if (!access_token) await getAccessToken();

    const requestOptions = {...options};
    requestOptions.headers = new Headers(options.headers || {});
    requestOptions.headers.set("Authorization", `Bearer ${access_token}`);

    let res = await fetch(url, requestOptions);
    if (res.status === 401) {
        access_token = null;
        await getAccessToken();
        requestOptions.headers.set("Authorization", `Bearer ${access_token}`);
        res = await fetch(url, requestOptions);
    }
    return res;
}

function handleProtectedFailure(error) {
    if (error.name === "AbortError") return;
    // A quota rejection (413) already shows its own message at the upload site;
    // it is not a service outage, so don't raise the Service Unavailable overlay.
    if (error.name === "QuotaExceeded") return;
    if (error.status === 401) {
        showLoginPage();
    } else if (error.status === 429 || error.status === 503) {
        showRateLimitOverlay(true, 20);
    } else if (!error.status || error.status >= 500) {
        showServiceOverlay(true);
    }
}

async function fetchProtected(url, options = {}) {
    let res;
    try {
        res = await fetchProtectedResponse(url, options);
    } catch (error) {
        handleProtectedFailure(error);
        throw error;
    }

    if (!res.ok) {
        const error = await parseResponseError(res);
        handleProtectedFailure(error);
        throw error;
    }
    return res.json();
}

async function login(username, password) {
    let res;
    try {
        res = await fetch("/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password }),
        });
    } catch (err) {
        // Network-level error
        throw err;
    }

    if (!res.ok) {
        let message = "Login failed";

        // Only attempt to parse JSON if response content-type is JSON
        const contentType = res.headers.get("content-type") || "";
        if (contentType.includes("application/json")) {
            const errData = await res.json();
            message = errData.error || message;
        } else if (res.statusText) {
            message = res.statusText;
        }

        const err = new Error(message);
        err.status = res.status; // always set HTTP status
        throw err;
    }

    // Attempt to get access token
    await getAccessToken(); // will throw if fails
}

async function logout() {
    try {
        await fetchProtected("/logout", {method: "POST"});
    } catch (err) {
        console.warn("Logout request failed:", err);
    } finally {
        access_token = null;
        showLoginPage();
    }
}

function showLoginPage() {
    const existingWrapper = document.querySelector(".container-wrapper");
    if (existingWrapper) existingWrapper.remove();

    const wrapper = document.createElement("div");
    wrapper.className = "container-wrapper";

    const template = document.getElementById("login-template");
    wrapper.appendChild(template.content.cloneNode(true));
    document.body.appendChild(wrapper);

    const form = wrapper.querySelector("#login-form");
    const errorDiv = wrapper.querySelector("#login-error");
    const submitButton = form.querySelector("#login-button");
    const inputs = form.querySelectorAll("input"); // all input fields

    const usernameInput = form.querySelector("input[name='username']");
    const passwordInput = form.querySelector("input[name='password']");

    // Enable button only when both fields are filled
    const toggleButtonState = () => {
        submitButton.disabled = !(usernameInput.value.trim() && passwordInput.value.trim());
    };

    usernameInput.addEventListener("input", toggleButtonState);
    passwordInput.addEventListener("input", toggleButtonState);

    // Password visibility toggle
    const toggle = wrapper.querySelector(".toggle-password");
    const eyeOpen = toggle.querySelector(".eye-open");
    const eyeClosed = toggle.querySelector(".eye-closed");

    toggle.addEventListener("click", () => {
        if (passwordInput.type === "password") {
            passwordInput.type = "text";
            toggle.setAttribute("aria-label", "Hide password");
            eyeClosed.classList.add("hidden");
            eyeOpen.classList.remove("hidden");
        } else {
            passwordInput.type = "password";
            toggle.setAttribute("aria-label", "Show password");
            eyeOpen.classList.add("hidden");
            eyeClosed.classList.remove("hidden");
        }
    });

    // Login form and button
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        errorDiv.textContent = "";

        // --- Check password length before sending ---
        if (passwordByteLength(passwordInput.value) > PASSWORD_MAX_BYTES) {
            showMessage("Password cannot exceed 72 UTF-8 bytes", errorDiv, 2000, "error");
            return;
        }

        submitButton.disabled = true; // disable to prevent multiple submits
        inputs.forEach(input => input.disabled = true);

        try {
            await login(form.username.value, form.password.value);
            initUplivion();
        } catch (err) {
            switch (err.status) {
                case 401:
                    showMessage("Invalid credentials", errorDiv, 2000, "error");
                    form.username.value = "";
                    form.password.value = "";
                    break;
                case 429:
                case 503:
                    showRateLimitOverlay(true, 20);
                    return;
                default:
                    showServiceOverlay(true);
                    return;
            }
        } finally {
            submitButton.disabled = false; // re-enable button regardless of outcome
            inputs.forEach(input => input.disabled = false);
        }
    });
}

async function initUplivion() {
    try {
        const res = await fetchProtected("/check", {method: "POST"});
        if (!res) return;

        currentUser = {
            user_id: res.user_id,
            username: res.username,
            first_name: res.first_name || "",
            last_name: res.last_name || "",
            role: res.role || "user",
        };

        const existingWrapper = document.querySelector(".container-wrapper");
        if (existingWrapper) existingWrapper.remove();

        const wrapper = document.createElement("div");
        wrapper.className = "container-wrapper";

        const template = document.getElementById("app-template");
        wrapper.appendChild(template.content.cloneNode(true));
        document.body.appendChild(wrapper);

        initializeElements(wrapper);
        attachEventListeners();
        resetUI();
        updateLinksButtonState();
        updateQuota();

        if (res.role === "admin" || res.role === "superadmin") {
            const adminBtn = wrapper.querySelector("#admin-btn");
            adminBtn.classList.remove("hidden");
        }

        requestAnimationFrame(() => {
            wrapper.style.visibility = "visible";
        });

        return wrapper;
    } catch (err) {
        console.error(err);
        return null; // explicitly return null so caller knows init failed
    }
}

// --- Main UI ---

// Quota - Fetch the current disk usage and update the stats in drop area
async function updateQuota() {
    const busy_overlay = busyOverlay("Updating disk quota...", 2000); // show busy overlay before starting
    try {
        const data = await fetchProtected("/quota");
        if (!data) return;

        const percent = data.total ? ((data.used / data.total) * 100).toFixed(1) : 0;
        quotaMessage.textContent = `Storage: ${formatFileSize(data.used)} / ${formatFileSize(data.total)} (${percent}%)`;
        return data;
    } catch (err) {
        console.error(err);
        quotaMessage.textContent = "Quota unavailable";
        return null;
    } finally {
        busy_overlay.cancel();  // stop the delayed overlay if it hasn't appeared
        busy_overlay.hide();  // ensure it's hidden if it already appeared
    }
}

// Checks if there are any links on the server and enables/disables the show links button accordingly
async function updateLinksButtonState() {
    try {
        const data = await fetchProtected("/links");
        if (!data) return;

        showLinksBtn.disabled = (data.length === 0);
    } catch (err) {
        console.error (err);
        showLinksBtn.disabled = true; // disable on error too
   }
}

// Drag & drop
    // Prevent browser from opening/dowloading files dropped outside the drop area
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => e.preventDefault());

    // When a file is dragged over the drop area
function dropAreaDragOver(e) {
    e.preventDefault();                 // allow drop inside this element
    dropArea.classList.add("highlight"); // add visual feedback
}

    // When the file leaves the drop area without dropping
function dropAreaDragLeave() {
    dropArea.classList.remove("highlight"); // remove highlight
}

    // When a file is dropped into the drop area
async function dropAreaDrop(e) {
    e.preventDefault();                   // prevent browser from opening the file
    dropArea.classList.remove("highlight"); // clear highlight

    // Enable the limit inputs
    uploadMaxDownloads.disabled = false;
    uploadExpiryValue.disabled = false;
    uploadExpiryUnit.disabled = false;

    // Reset UI for new file
    resetUI();

    if (e.dataTransfer.files.length) {
        const file = e.dataTransfer.files[0]; // first file only

        // Save globally for upload
        selectedFile = file;

        // Show file details
        document.getElementById("file-name").textContent = file.name;
        document.getElementById("file-size").textContent = formatFileSize(file.size);
    }
}

// Click to browse
    // Called when the user clicks on the drop area
function dropAreaClick() {
    // Enable the limit inputs and dropdown (they may be disabled initially)
    uploadMaxDownloads.disabled = false;
    uploadExpiryValue.disabled = false;
    uploadExpiryUnit.disabled = false;

    resetUI();

    // This shows the system file picker dialog
    fileInput.click();
}

    // Called when the user selects a file using the file input
async function dropAreaFileChange() {
    // Make sure a file was actually selected
    if (fileInput.files.length) {
        const file = fileInput.files[0]; // only take the first file

        // Save the file into a global variable so other parts of the code (like upload) can access it
        selectedFile = file;

        // Show file information to the user
        document.getElementById("file-name").textContent = file.name;
        document.getElementById("file-size").textContent = formatFileSize(file.size);
    }
}

// Checks if there are any selected file and a valid expiry value, and enables/disables the upload button accordingly
function validateUpload() {
    // The upload button arms (and turns green) only once a file is chosen and
    // both limits — max downloads and expiry — carry a valid positive value.
    const expirySeconds = getExpirySeconds(uploadExpiryValue, uploadExpiryUnit);
    const maxDownloads = parseInt(uploadMaxDownloads.value, 10);
    const maxDownloadsSet = uploadMaxDownloads.value.trim() !== "" && maxDownloads > 0;
    uploadBtn.disabled =
        !selectedFile || selectedFile.size === 0 || expirySeconds <= 0 || !maxDownloadsSet;
}

// Helper function to handle file uploads
async function upload(file, expirySeconds, signal, overwrite = 0, maxDownloads = "") {
    const chunkSize = 5 * 1024 * 1024; // 5 MB
    let uploadedBytes = 0;
    let activeUploadTime = 0;
    let uploadedThisSession = 0;
    let lastChunkTime = null;

    let uploadedChunks = new Set();
    let totalChunks = Math.ceil(file.size / chunkSize);
    const resumeKey = `uplivion-upload:${file.name}:${file.size}:${file.lastModified}`;
    let uploadID = sessionStorage.getItem(resumeKey);

    // --- Step 1: Fetch upload progress from backend ---
    try {
        if (!uploadID) throw new Error("No resumable upload");
        const json = await fetchProtected("/progress", {
            headers: { "X-Upload-ID": uploadID }
        });

        // Use the fileID from the backend for this upload
        if (json.fileID) {
            uploadID = json.fileID;
            activeUploadID = uploadID;
        }

        uploadedChunks = new Set(json.uploadedChunks || []);

        // calculate already uploaded bytes
        uploadedBytes = Array.from(uploadedChunks).reduce((acc, idx) => {
            const start = idx * chunkSize;
            const end = Math.min(file.size, start + chunkSize);
            return acc + (end - start);
        }, 0);

//        const percent = (uploadedBytes / file.size) * 100;
//        progressBar.style.width = percent.toFixed(2) + "%";
//        progressBarText.textContent = `${percent.toFixed(0)}%`;

    } catch (e) {
        if (uploadID && (e.status === 400 || e.status === 404)) {
            sessionStorage.removeItem(resumeKey);
            uploadID = null;
            activeUploadID = null;
        }
        console.warn("Could not fetch upload progress:", e);
    }

    // --- Step 2: Upload remaining chunks ---
    for (let index = 0; index < totalChunks; index++) {
        if (signal?.aborted) throw new DOMException("Upload canceled", "AbortError");

         while (isPaused) {
            progressBar.style.backgroundColor = "#cc9a06";
            progressBarText.textContent = "Upload Paused";
            await new Promise(resolve => setTimeout(resolve, 500)); // wait until resumed
        }

        if (uploadedChunks.has(index)) continue; // skip already uploaded

        const start = index * chunkSize;
        const end = Math.min(file.size, start + chunkSize);
        const chunk = file.slice(start, end);

        const formData = new FormData();
        formData.append("file", chunk);
        formData.append("fileName", file.name);
        formData.append("chunkIndex", index);
        formData.append("chunkOffset", start);
        formData.append("totalChunks", totalChunks);
        formData.append("fileSize", file.size);
        formData.append("chunkSize", chunkSize);
        if (uploadID) formData.append("uploadID", uploadID);

        formData.append("expires", expirySeconds);
        if (maxDownloads !== "") formData.append("maxDownloads", maxDownloads);
        formData.append("overwrite", overwrite);

        lastChunkTime = Date.now();

        let res, json;
        try {
            let attempts = 0;
            const maxRetries = 3;
            while (attempts < maxRetries) {
                try {
                    res = await fetchProtectedResponse("/upload", {
                        method: "POST",
                        body: formData,
                        signal
                    });
                    break; // success
                } catch (err) {
                    if (err.name === "AbortError") throw err;
                    attempts++;
                    if (attempts === maxRetries) throw err;
                }
            }

            json = await res.json();

            if (res.status === 409) {
                // A conflict returns the completed object's display ID, not a
                // resumable .part ID. Start the confirmed overwrite cleanly.
                sessionStorage.removeItem(resumeKey);
                activeUploadID = null;
                const overwriteFile = await overwriteOverlay(json.filename, json.size, file.size, json.uploaded, json.hash);
                if (!overwriteFile) return;
                return await upload(file, expirySeconds, signal, 1, maxDownloads);
            }

            if (json.fileID) {
                uploadID = json.fileID;
                activeUploadID = uploadID;
                sessionStorage.setItem(resumeKey, uploadID);
            }

            if (res.status === 413) {
                showMessage("This file exceeds your storage quota!", dropAreaMessageText, 2000, "error");
                uploadBtn.classList.remove("hidden");
                cancelBtn.classList.add("hidden");
                enableDropAreaAndInput();
                throw new DOMException(json.error, "QuotaExceeded");
            }

            if (!res.ok) throw await parseResponseError(res);

        } catch (err) {
            handleProtectedFailure(err);
            console.error("Upload failed:", err);
            throw err;
        }

        // --- Update progress bar ---
        const now = Date.now();
        activeUploadTime += (now - lastChunkTime) / 1000;
        uploadedBytes += end - start;
        uploadedThisSession += end - start;

        const percent = (uploadedBytes / file.size) * 100;
        progressBarText.textContent = `${percent.toFixed(0)}%`;
        progressBar.style.width = percent.toFixed(2) + "%";
        progressBar.style.backgroundColor = "#17B169";

        const avgSpeed = uploadedThisSession / activeUploadTime;
        const remainingBytes = file.size - uploadedBytes;
        const eta = avgSpeed > 0 ? remainingBytes / avgSpeed : 0;
        quotaMessage.textContent = `Estimated Time: ${formatTime(eta)} (${formatSpeed(avgSpeed)})`;

        if (json.url) {
            uploadedLink = { filename: file.name, url: json.url };
            sessionStorage.removeItem(resumeKey);
            activeUploadID = null;
        }
    }
}

// Upload button
async function uploadButton() {
    if (!selectedFile) return;

    // Disable UI elements
    progressBar.style.backgroundColor = "#17B169";
    disableDropAreaAndInput();
    uploadBtn.disabled = true;
    uploadMaxDownloads.disabled = true;
    uploadExpiryValue.disabled = true;
    uploadExpiryUnit.disabled = true;

    // Create controller for cancellation
    const controller = new AbortController();

    // Swap Upload button to Cancel button
    uploadBtn.classList.add("hidden");
    cancelBtn.classList.remove("hidden");
    pauseBtn.classList.remove("hidden");
    resumeBtn.classList.add("hidden");
    copyBtn.classList.add("hidden");

    // Cancel click handler
    const cancelHandler = async () => {
        controller.abort();
        try {
            await fetchProtected("/cancel", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({uploadID: activeUploadID})
            });
            isPaused = false;
        } catch (err) {
            console.error("Failed to notify backend of cancellation:", err);
        }
    };
    cancelBtn.addEventListener("click", cancelHandler, { once: true });

    // Pause handler
    pauseBtn.onclick = () => {
        isPaused = true;
        pauseBtn.classList.add("hidden");
        resumeBtn.classList.remove("hidden");
        copyBtn.classList.add("hidden");
    };

    // Resume handler
    resumeBtn.onclick = async () => {
        isPaused = false;
        pauseBtn.classList.remove("hidden");
        resumeBtn.classList.add("hidden");
        copyBtn.classList.add("hidden");
    };

    try {
        // Upload file
        const expirySeconds = getExpirySeconds(uploadExpiryValue, uploadExpiryUnit);
        const maxDownloads = uploadMaxDownloads.value.trim();
        await upload(selectedFile, expirySeconds, controller.signal, 0, maxDownloads);

        // Success UI updates
        copyBtn.disabled = false;
        showLinksBtn.disabled = false;
        progressBarText.textContent = "File link is ready";
        progressBar.style.backgroundColor = "#17B169";
    } catch (err) {
        if (err.name === "AbortError") {
            progressBar.style.backgroundColor = "#FF4D4F";
            progressBar.style.width = "100%";
            progressBarText.textContent = "Upload Canceled";
        } else if (err.name === "QuotaExceeded") {
            progressBarText.textContent = "";
        } else {
            console.error(err);
            progressBar.style.backgroundColor = "#FF4D4F";
            progressBar.style.width = "100%";
            progressBarText.textContent = "Upload Failed";
        }
    } finally {
        // Restore buttons and update quota
        await updateQuota();
        enableDropAreaAndInput();
        pauseBtn.classList.add("hidden");
        resumeBtn.classList.add("hidden");
        copyBtn.classList.remove("hidden");
        uploadMaxDownloads.disabled = false;
        uploadExpiryValue.disabled = false;
        uploadExpiryUnit.disabled = false;
        uploadBtn.classList.remove("hidden");
        cancelBtn.classList.add("hidden");
    }
}

// Helper function to copy link to clipboard
function copyLinkToClipboard(text, targetMsg, successMsg = "Link copied!", errorMsg = "Failed to copy!") {
    if (!text) {
        showMessage(errorMsg, targetMsg, 2000, "error");
        return;
    }

    navigator.clipboard.writeText(text)
        .then(() => showMessage(successMsg, targetMsg, 2000, "copied"))
        .catch(err => {
            console.error(err);
            showMessage(errorMsg, targetMsg, 2000, "error");
        });
}

// Copy button
function copyButton() {
    if (!uploadedLink || !uploadedLink.url) {
        showMessage("No valid link to copy!", dropAreaMessageText, 2000, "error");
        return;
    }

    copyLinkToClipboard(uploadedLink.url, dropAreaMessageText);
}



// --- File manager (links list) ---

// Open Modal
function openLinksModal() {
    linksOverlay.classList.add("active");
    document.body.classList.add("modal-open");

    // Push modal state into history to allow back button function
    history.pushState({ modal: true }, "", "#links");
}

// Close Modal (also closes the settings overlay and clears the selection)
function closeLinksModal() {
    closeLinkSettings();
    linksOverlay.classList.remove("active");
    document.body.classList.remove("modal-open");
    linksSelectedId = null;

    // Clean history if we're in modal state
    if (history.state && history.state.modal) {
        history.replaceState(null, "", window.location.pathname);
    }
}

// Download count for display: just the number, or "3 / 5" when capped. A label
// or a down-arrow icon supplies the "downloads" meaning at each call site.
function formatDownloads(count, max) {
    const c = Number(count) || 0;
    if (max === null || max === undefined || max === "") {
        return String(c);
    }
    return `${c} / ${max}`;
}

// Small inline metric icons for the card detail row (stroke = currentColor, so
// they tint with the surrounding status colour).
const CLOCK_ICON_SVG =
    '<svg class="link-detail-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 1.8"/></svg>';
const DOWNLOAD_ICON_SVG =
    '<svg class="link-detail-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
const SIZE_ICON_SVG =
    '<svg class="link-detail-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/><path d="M3 12c0 1.66 4 3 9 3s9-1.34 9-3"/></svg>';

// A detail cell: a leading icon (static, safe markup) plus a text value carried
// as a text node so a value can never be interpreted as markup.
function linkDetailCell(iconSvg, value, className) {
    const span = document.createElement("span");
    if (className) span.className = className;
    if (iconSvg) span.innerHTML = iconSvg;
    span.append(document.createTextNode(value));
    return span;
}

// The currently selected file, or null.
function selectedLink() {
    return currentLinks.find((l) => l.file_id === linksSelectedId) || null;
}

// The active ordering, read straight from the sort filter.
function currentSortValue() {
    const sel = linksModal.querySelector("#sort-links");
    return sel ? sel.value : "created-desc";
}

// The toolbar (Copy · Settings) acts on the selected file, so it stays disabled
// until a card is picked.
function updateLinksToolbar() {
    const has = !!selectedLink();
    const copyBtn = linksModal.querySelector("#links-copy");
    const settingsBtn = linksModal.querySelector("#links-settings");
    if (copyBtn) copyBtn.disabled = !has;
    if (settingsBtn) settingsBtn.disabled = !has;
}

// Single-card selection: clicking the selected card clears it.
function selectLink(fileId) {
    linksSelectedId = linksSelectedId === fileId ? null : fileId;
    for (const row of linksList.querySelectorAll(".link-item")) {
        row.classList.toggle("selected", row.dataset.fileId === linksSelectedId);
    }
    updateLinksToolbar();
}

// Truncate text in the middle with an ellipsis so it fits in maxWidth px of the
// given font (preserving the head and tail — e.g. a file's extension). Uses a
// canvas so it needs no layout of its own.
function middleEllipsis(text, ctx, font, maxWidth) {
    ctx.font = font;
    if (maxWidth <= 0 || ctx.measureText(text).width <= maxWidth) return text;
    const ell = "…";
    for (let keep = text.length - 1; keep >= 2; keep--) {
        const head = Math.ceil(keep / 2);
        const tail = keep - head;
        const candidate = text.slice(0, head) + ell + text.slice(text.length - tail);
        if (ctx.measureText(candidate).width <= maxWidth) return candidate;
    }
    return text.slice(0, 1) + ell;
}

// Middle-truncate every visible card name to fit its (two-row) box. Runs after
// the modal is shown and on resize, when the name column has a real width.
function applyCardNameEllipsis() {
    const names = linksList.querySelectorAll(".link-item-name");
    if (!names.length) return;
    const ctx = applyCardNameEllipsis._ctx
        || (applyCardNameEllipsis._ctx = document.createElement("canvas").getContext("2d"));
    for (const el of names) {
        const full = el.dataset.full || "";
        const lineWidth = el.clientWidth;
        if (lineWidth <= 0) { el.textContent = full; continue; }
        const cs = getComputedStyle(el);
        const font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
        // Two rows of capacity, with a small margin for imperfect wrapping.
        el.textContent = middleEllipsis(full, ctx, font, lineWidth * 2 * 0.93);
    }
}

// Card creation: [dot] [main] [notes]. The main column is name (middle-
// truncated, two rows) then a single line of stats (expiry, downloads, size).
// The notes sit in a fixed field on the right (it scrolls if long, or shows a
// placeholder when empty).
function createLinkItem(item) {
    const div = document.createElement("div");
    div.className = "link-item";
    div.dataset.fileId = item.file_id;
    div.setAttribute("role", "button");
    div.tabIndex = 0;
    if (item.file_id === linksSelectedId) div.classList.add("selected");

    const dot = document.createElement("span");
    dot.className = "link-select-dot";
    dot.setAttribute("aria-hidden", "true");

    const main = document.createElement("div");
    main.className = "link-item-main";

    const name = document.createElement("div");
    name.className = "link-item-name";
    name.dataset.full = item.filename;
    name.textContent = item.filename;   // middle-truncated by applyCardNameEllipsis

    const stats = document.createElement("div");
    stats.className = "link-item-stats";

    const expiresIn = Number(item.expires_in) || 0;
    let expiryClass = "";
    if (item.revoked) expiryClass = "link-status-revoked";
    else if (expiresIn <= 0) expiryClass = "link-status-expired";

    // A capped link with no downloads left flags just that cell red.
    const downloadsExhausted = item.max_downloads !== null && item.max_downloads !== undefined
        && Number(item.download_count) >= Number(item.max_downloads);

    // One line: expiry-countdown, downloads, size.
    const expiryCell = linkDetailCell(
        CLOCK_ICON_SVG, formatExpiry(expiresIn, item.revoked), expiryClass
    );
    const downloadsCell = linkDetailCell(
        DOWNLOAD_ICON_SVG, formatDownloads(item.download_count, item.max_downloads),
        downloadsExhausted ? "link-status-capped" : ""
    );
    const sizeCell = linkDetailCell(SIZE_ICON_SVG, formatFileSize(Number(item.size) || 0));
    stats.append(expiryCell, downloadsCell, sizeCell);

    // A per-card status line (e.g. "Link copied!") in the free row between
    // name and stats — see linksCopySelected().
    const cardMsg = document.createElement("div");
    cardMsg.className = "link-item-msg";
    cardMsg.setAttribute("role", "status");

    main.append(name, cardMsg, stats);

    const notes = document.createElement("div");
    notes.className = "link-item-notes";
    if (item.notes) {
        notes.textContent = item.notes;   // safe (textContent)
    } else {
        notes.classList.add("empty");
        notes.textContent = "No notes";
    }

    div.append(dot, main, notes);

    div.addEventListener("click", () => selectLink(item.file_id));
    div.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            selectLink(item.file_id);
        }
    });

    return div;
}

// --- Toolbar actions (operate on the selected file) ---

function linksCopySelected() {
    const item = selectedLink();
    if (!item) return;
    const card = Array.from(linksList.querySelectorAll(".link-item"))
        .find((el) => el.dataset.fileId === item.file_id);
    const target = card
        ? card.querySelector(".link-item-msg")
        : linksModal.querySelector("#links-msg");
    copyLinkToClipboard(item.link, target);
}

function linksSettingsSelected() {
    const item = selectedLink();
    if (!item) return;
    openLinkSettings(item.file_id);
}

// --- Settings overlay (per-file settings + revoke/delete) ---

function openLinkSettingsOverlay() {
    document.querySelector("#links-settings-overlay").classList.remove("hidden");
}

function closeLinkSettings() {
    document.querySelector("#links-settings-overlay").classList.add("hidden");
    document.querySelector("#links-settings-form").innerHTML = "";
}

async function openLinkSettings(fileId) {
    const busy_overlay = busyOverlay("Loading settings...");
    try {
        const item = await fetchProtected(`/links/${fileId}`);
        if (!item) return;
        renderLinkSettings(item);
        openLinkSettingsOverlay();
    } catch (err) {
        console.error(err);
        showMessage("Error loading settings", linksModal.querySelector("#links-msg"), 2000, "error");
    } finally {
        busy_overlay.cancel();
        busy_overlay.hide();
    }
}

function renderLinkSettings(item) {
    const form = document.querySelector("#links-settings-form");
    const expiresIn = Number(item.expires_in) || 0;
    const statusText = item.revoked ? "Revoked" : (expiresIn <= 0 ? "Expired" : "Active");
    const statusClass = item.revoked
        ? "link-status-revoked"
        : (expiresIn <= 0 ? "link-status-expired" : "link-status-active");
    const expiryClass = item.revoked
        ? "link-status-revoked"
        : (expiresIn <= 0 ? "link-status-expired" : "");
    const maxVal = (item.max_downloads === null || item.max_downloads === undefined)
        ? "" : String(item.max_downloads);
    // A live link keeps its current expiry when the field is left blank; an
    // expired or revoked link must be given a new expiry to come back to life.
    const expiryRequired = !!item.revoked || expiresIn <= 0;

    form.innerHTML = `
        <div class="links-settings-header">
            <h3>Link Settings</h3>
            <div class="links-settings-toolbar">
                <button type="button" id="links-save" class="admin-tool links-tool-save" title="Save changes" aria-label="Save changes">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>
                </button>
                <button type="button" id="links-revoke" class="admin-tool" title="Revoke link" aria-label="Revoke link">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/></svg>
                </button>
                <button type="button" id="links-delete" class="admin-tool admin-tool-danger" title="Delete file" aria-label="Delete file">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
                </button>
            </div>
        </div>
        <div class="links-settings-body">
            <div class="links-info">
                <div class="link-url-row">
                    <input type="text" id="links-detail-url" readonly>
                    <button type="button" id="links-detail-copy" class="admin-tool" title="Copy link" aria-label="Copy link">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                    </button>
                </div>
                <div class="links-info-grid">
                    <span>Status</span><span class="${statusClass}">${statusText}</span>
                    <span>Downloads</span><span>${escapeHtml(formatDownloads(item.download_count, item.max_downloads))}</span>
                    <span>Size</span><span>${escapeHtml(formatFileSize(Number(item.size) || 0))}</span>
                    <span>Created</span><span>${escapeHtml(item.created || "")}</span>
                    <span>Expiry</span><span class="${expiryClass}">${escapeHtml(formatExpiryDate(expiresIn, item.revoked))}</span>
                </div>
            </div>
            <div class="links-limits-row">
                <div class="links-limit-group links-limit-max">
                    <label class="links-field-label" for="links-max-downloads">Max downloads</label>
                    <input type="text" id="links-max-downloads" placeholder="Unlimited" maxlength="9" inputmode="numeric">
                </div>
                <div class="links-limit-group links-limit-expiry">
                    <label class="links-field-label" for="links-expiry-value">Expiry</label>
                    <div class="links-expiry-input">
                        <input type="text" id="links-expiry-value" placeholder="${expiryRequired ? "Set expiry" : "Keep current"}" maxlength="5" inputmode="numeric">
                        <select id="links-expiry-unit">
                            <option value="days" selected>Days</option>
                            <option value="hours">Hours</option>
                            <option value="minutes">Minutes</option>
                            <option value="seconds">Seconds</option>
                        </select>
                    </div>
                </div>
            </div>
            <label class="links-field-label" for="links-notes">Notes</label>
            <textarea id="links-notes" placeholder="Add a note" maxlength="500" rows="4"></textarea>
            <div id="links-settings-msg"></div>
        </div>
    `;

    // Whether a save must carry an expiry (dead links only) is read back in
    // saveLinkSettings.
    form.dataset.expiryRequired = expiryRequired ? "1" : "";

    // Set the link, current cap, and notes via properties (never markup) so a
    // filename / URL / note can't inject anything and the page CSP stays happy.
    form.querySelector("#links-detail-url").value = item.link;
    form.querySelector("#links-max-downloads").value = maxVal;
    form.querySelector("#links-notes").value = item.notes || "";

    const expEl = form.querySelector("#links-expiry-value");
    expEl.addEventListener("input", () => {
        expEl.value = expEl.value.replace(/\D/g, "").slice(0, 5);
    });
    const maxEl = form.querySelector("#links-max-downloads");
    maxEl.addEventListener("input", () => {
        maxEl.value = maxEl.value.replace(/\D/g, "").slice(0, 9);
    });

    form.querySelector("#links-detail-copy").onclick = () =>
        copyLinkToClipboard(item.link, form.querySelector("#links-settings-msg"));

    const revokeBtn = form.querySelector("#links-revoke");
    revokeBtn.disabled = !!item.revoked;
    revokeBtn.onclick = () => revokeLink(item.file_id);
    form.querySelector("#links-delete").onclick = () => deleteLink(item.file_id);
    form.querySelector("#links-save").onclick = () => saveLinkSettings(item.file_id);
}

// Refresh the cached list entry with a server row.
function replaceLinkInCache(updated) {
    const idx = currentLinks.findIndex((l) => l.file_id === updated.file_id);
    if (idx >= 0) currentLinks[idx] = updated;
}

async function saveLinkSettings(fileId) {
    const form = document.querySelector("#links-settings-form");
    const msg = form.querySelector("#links-settings-msg");
    showMessage("", msg, 0, "");

    const expEl = form.querySelector("#links-expiry-value");
    const unitEl = form.querySelector("#links-expiry-unit");
    const maxEl = form.querySelector("#links-max-downloads");
    const notesEl = form.querySelector("#links-notes");

    const body = {
        // Blank clears the cap (unlimited); a number sets it.
        max_downloads: maxEl.value.trim() === "" ? null : Number(maxEl.value),
        // Notes are always sent (a blank value clears them).
        notes: notesEl.value,
    };

    // Expiry: a live link keeps its current expiry when the field is blank; an
    // expired/revoked link must be given one. A provided value renews the token.
    const expiryRequired = form.dataset.expiryRequired === "1";
    if (expEl.value.trim() !== "") {
        const secs = getExpirySeconds(expEl, unitEl);
        if (secs <= 0) {
            showMessage("Enter a valid expiry", msg, 0, "error");
            return;
        }
        body.expiry = secs;
    } else if (expiryRequired) {
        showMessage("This link is expired or revoked — set an expiry to renew it", msg, 0, "error");
        return;
    }

    const busy_overlay = busyOverlay("Saving...");
    try {
        const updated = await fetchProtected(`/links/${fileId}/settings`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        if (!updated) return;
        replaceLinkInCache(updated);
        renderLinkSettings(updated);
        renderLinksList(currentLinks, currentSortValue());
        // renderLinkSettings() just replaced the form's contents, so the message
        // element must be re-queried rather than reusing the pre-render `msg`.
        showMessage("Settings saved", form.querySelector("#links-settings-msg"), 2000, "regenerated");
    } catch (err) {
        showMessage(err.message || "Failed to save settings", msg, 0, "error");
    } finally {
        busy_overlay.cancel();
        busy_overlay.hide();
    }
}

async function revokeLink(fileId) {
    const busy_overlay = busyOverlay("Revoking link...", 300);
    try {
        const updated = await fetchProtected(`/links/${fileId}/revoke`, { method: "POST" });
        if (!updated) return;
        replaceLinkInCache(updated);
        renderLinkSettings(updated);
        renderLinksList(currentLinks, currentSortValue());
        // If this is the file just uploaded in the main UI, its link is now dead.
        if (uploadedLink && uploadedLink.filename === updated.filename) resetUI();
        // renderLinkSettings() just replaced the form's contents, so the message
        // element must be re-queried rather than reusing one from before the render.
        const settingsMsg = document.querySelector("#links-settings-form #links-settings-msg");
        showMessage("Link revoked", settingsMsg, 2000, "revoked");
    } catch (err) {
        console.error(err);
        const form = document.querySelector("#links-settings-form");
        const msg = form && form.querySelector("#links-settings-msg");
        if (msg) showMessage(err.message || "Failed to revoke link", msg, 0, "error");
    } finally {
        busy_overlay.cancel();
        busy_overlay.hide();
    }
}

async function deleteLink(fileId) {
    const item = currentLinks.find((l) => l.file_id === fileId);
    const name = item ? item.filename : "this file";
    const ok = await confirmDialog(`Delete "${name}"? This removes the file and its link permanently.`, "Delete");
    if (!ok) return;

    const busy_overlay = busyOverlay("Deleting file...");
    try {
        const res = await fetchProtected(`/links/${fileId}`, { method: "DELETE" });
        if (!res) return;

        currentLinks = currentLinks.filter((l) => l.file_id !== fileId);
        if (linksSelectedId === fileId) linksSelectedId = null;
        closeLinkSettings();
        renderLinksList(currentLinks, currentSortValue());
        updateQuota();
        // If the deleted file is the one just uploaded in the main UI, reset it.
        if (uploadedLink && item && uploadedLink.filename === item.filename) resetUI();
    } catch (err) {
        console.error(err);
        const form = document.querySelector("#links-settings-form");
        const msg = form && form.querySelector("#links-settings-msg");
        if (msg) showMessage(err.message || "Failed to delete file", msg, 0, "error");
    } finally {
        busy_overlay.cancel();
        busy_overlay.hide();
    }
}

// Render the list: keep the sort filter and re-apply the current selection.
function renderLinksList(data = currentLinks, sortOption = currentSortValue()) {
    // Clear list except header
    linksList.querySelectorAll(".link-item, .placeholder").forEach(el => el.remove());

    // Drop a stale selection if that file is no longer present.
    if (linksSelectedId && !data.some((l) => l.file_id === linksSelectedId)) {
        linksSelectedId = null;
    }

    // Show empty list message
    if (data.length === 0) {
        const placeholder = document.createElement("div");
        placeholder.className = "link-item placeholder";
        placeholder.innerHTML = `<span class="empty-message">No uploaded files yet</span>`;
        linksList.appendChild(placeholder);
        updateLinksToolbar();
        return;
    }

    // Sort
    const sorted = [...data];
    switch (sortOption) {
        case "created-asc":
            sorted.sort((a, b) => new Date(a.created) - new Date(b.created));
            break;
        case "created-desc":
            sorted.sort((a, b) => new Date(b.created) - new Date(a.created));
            break;
        case "size-asc":
            sorted.sort((a, b) => Number(a.size) - Number(b.size));
            break;
        case "size-desc":
            sorted.sort((a, b) => Number(b.size) - Number(a.size));
            break;
    }

    // Generate each item
    sorted.forEach(item => {
        linksList.appendChild(createLinkItem(item));
    });
    updateLinksToolbar();
    // Middle-truncate the names now that the cards are in the (visible) list.
    applyCardNameEllipsis();
}

// Show the file manager.
async function showLinksButton() {
    const busy_overlay = busyOverlay("Fetching links..."); // show busy overlay before starting
    try {
        const data = await fetchProtected("/links");
        if (!data) return;

        currentLinks = data; // store current state
        linksSelectedId = null;
        linksList.innerHTML = "";
        // Open first so the cards have a real width when names are truncated.
        openLinksModal();
        renderLinksList(currentLinks, currentSortValue());
        wireLinksControls();
    } catch (err) {
        console.error(err);
        showMessage("Error fetching links list", dropAreaMessageText, 2000, "error");
    } finally {
        busy_overlay.cancel();  // stop the delayed overlay if it hasn't appeared
        busy_overlay.hide();  // ensure it's hidden if it already appeared
    }
}

// Wire the toolbar, sort filter, and settings-overlay backdrop. Idempotent:
// button handlers are (re)assigned each open; the once-only listeners guard
// against stacking.
function wireLinksControls() {
    const copyBtn = linksModal.querySelector("#links-copy");
    const settingsBtn = linksModal.querySelector("#links-settings");
    if (copyBtn) copyBtn.onclick = linksCopySelected;
    if (settingsBtn) settingsBtn.onclick = linksSettingsSelected;

    const sortDropdown = linksModal.querySelector("#sort-links");
    if (sortDropdown && !sortDropdown.dataset.listenerAttached) {
        sortDropdown.addEventListener("change", (e) => {
            renderLinksList(currentLinks, e.target.value);
        });
        sortDropdown.dataset.listenerAttached = "true"; // prevent double attachment
    }

    const settingsOverlay = document.querySelector("#links-settings-overlay");
    if (settingsOverlay && !settingsOverlay.dataset.listenerAttached) {
        settingsOverlay.addEventListener("click", (e) => {
            if (e.target === settingsOverlay) closeLinkSettings();
        });
        settingsOverlay.dataset.listenerAttached = "true";
    }
}



// --- Admin Panel ---

function openAdminPanel() {
    const overlay = document.querySelector("#admin-overlay");
    overlay.classList.remove("hidden");
    document.body.classList.add("modal-open");

    adminSelectedId = null;

    // Close by clicking the backdrop (outside the modal); there is no × button.
    overlay.onclick = (e) => {
        if (e.target === overlay) closeAdminPanel();
    };

    // Wire the toolbar. Add User creates; the rest act on the selected row.
    overlay.querySelector("#admin-add-user").onclick = showAddUserForm;
    overlay.querySelector("#admin-toggle-status").onclick = adminToggleStatusSelected;
    overlay.querySelector("#admin-toggle-role").onclick = adminToggleRoleSelected;
    overlay.querySelector("#admin-reset-pw").onclick = adminResetPasswordSelected;
    overlay.querySelector("#admin-set-quota").onclick = adminSetQuotaSelected;
    overlay.querySelector("#admin-delete-files").onclick = adminDeleteFilesSelected;
    overlay.querySelector("#admin-delete-user").onclick = adminDeleteUserSelected;

    const formOverlay = document.querySelector("#admin-form-overlay");
    formOverlay.onclick = (e) => {
        if (e.target === formOverlay) closeAdminForm();
    };

    closeAdminForm();
    showAdminError("");

    loadAdminUsers();
}

function openAdminForm() {
    document.querySelector("#admin-form-overlay").classList.remove("hidden");
}

function closeAdminForm() {
    document.querySelector("#admin-form-overlay").classList.add("hidden");
    document.querySelector("#admin-form").innerHTML = "";
}

function closeAdminPanel() {
    const overlay = document.querySelector("#admin-overlay");
    overlay.classList.add("hidden");
    document.body.classList.remove("modal-open");
    // An admin may have changed their own quota; refresh the main UI display.
    updateQuota();
}

function showAdminError(msg) {
    const el = document.querySelector("#admin-msg");
    if (el) el.textContent = msg || "";
}

async function loadAdminUsers() {
    const list = document.querySelector("#admin-user-list");
    list.textContent = "Loading...";
    showAdminError("");
    try {
        const users = await fetchProtected("/admin/users");
        if (!users) return;
        renderAdminUsers(users);
    } catch (err) {
        list.textContent = "Failed to load users";
    }
}

function selectedAdminUser() {
    return adminUsersCache.find((u) => u.user_id === adminSelectedId) || null;
}

// Enable/disable the toolbar and reflect the selected user's current state.
// Actions the server would reject (delete self, demote/disable the last active
// admin) are disabled here rather than allowed through and surfaced as an error.
function updateAdminToolbar() {
    const user = selectedAdminUser();
    const statusBtn = document.querySelector("#admin-toggle-status");
    const roleBtn = document.querySelector("#admin-toggle-role");
    const resetBtn = document.querySelector("#admin-reset-pw");
    const quotaBtn = document.querySelector("#admin-set-quota");
    const filesBtn = document.querySelector("#admin-delete-files");
    const deleteBtn = document.querySelector("#admin-delete-user");
    const all = [statusBtn, roleBtn, resetBtn, quotaBtn, filesBtn, deleteBtn];

    // Nothing selected: every selection-dependent tool is off.
    if (!user) {
        for (const btn of all) {
            if (btn) btn.disabled = true;
        }
        statusBtn.classList.remove("admin-tool-on");
        roleBtn.classList.remove("admin-tool-on");
        return;
    }

    const callerIsSuper = !!(currentUser && currentUser.role === "superadmin");
    const isSelf = !!(currentUser && user.user_id === currentUser.user_id);
    const targetIsSuper = user.role === "superadmin";

    // A regular admin manages only regular users; a superadmin manages everyone.
    // A row the caller can't manage (a higher/peer tier seen by an admin) shows
    // read-only — every action disabled — mirroring the server's target guard.
    const manageable = callerIsSuper || user.role === "user";

    // The system must always keep one active superadmin (the CLI-rooted tier),
    // so it can't be disabled or deleted when it is the last one left.
    const activeSupers = adminUsersCache.filter(
        (u) => u.role === "superadmin" && u.status === "active"
    ).length;
    const lastActiveSuper = targetIsSuper && user.status === "active" && activeSupers <= 1;

    if (manageable) {
        resetBtn.disabled = false;
        quotaBtn.disabled = false;
        filesBtn.disabled = false;
        // Can't delete yourself or the last active superadmin.
        deleteBtn.disabled = isSelf || lastActiveSuper;
        // Only a superadmin changes roles, and never a superadmin's own tier.
        roleBtn.disabled = !callerIsSuper || targetIsSuper;
        // Enabling is always fine; disabling the last active superadmin is not.
        statusBtn.disabled = lastActiveSuper;
    } else {
        for (const btn of [statusBtn, roleBtn, resetBtn, quotaBtn, filesBtn, deleteBtn]) {
            btn.disabled = true;
        }
    }

    const statusTip = user.status === "active" ? "Disable user" : "Enable user";
    statusBtn.title = statusTip;
    statusBtn.setAttribute("aria-label", statusTip);
    statusBtn.classList.toggle("admin-tool-on", user.status !== "active");

    const roleTip = user.role === "admin" ? "Demote to user" : "Promote to admin";
    roleBtn.title = roleTip;
    roleBtn.setAttribute("aria-label", roleTip);
    // Highlight elevated rows (admin or superadmin).
    roleBtn.classList.toggle("admin-tool-on", user.role === "admin" || targetIsSuper);
}

// Single-row selection: clicking a selected row clears it.
function selectAdminUser(userId) {
    adminSelectedId = adminSelectedId === userId ? null : userId;
    closeAdminForm();
    showAdminError("");
    for (const row of document.querySelectorAll(".admin-user-row")) {
        row.classList.toggle("selected", row.dataset.userId === adminSelectedId);
    }
    updateAdminToolbar();
}

function renderAdminUsers(users) {
    adminUsersCache = users;
    if (adminSelectedId && !users.some((u) => u.user_id === adminSelectedId)) {
        adminSelectedId = null;
    }
    const list = document.querySelector("#admin-user-list");
    list.innerHTML = "";
    if (!users.length) {
        list.innerHTML = `<div class="admin-empty">No users yet.</div>`;
        updateAdminToolbar();
        return;
    }
    for (const u of users) {
        const row = document.createElement("div");
        row.className = "admin-user-row";
        row.dataset.userId = u.user_id;
        row.setAttribute("role", "button");
        row.tabIndex = 0;
        if (u.user_id === adminSelectedId) row.classList.add("selected");

        const usedBytes = Number(u.used_bytes) || 0;
        const quotaBytes = Number(u.quota_bytes) || 0;
        const fileCount = Number(u.file_count) || 0;
        // Clamp to a finite 0–100 so a missing/garbage value can never leave the
        // fill without a valid width (which would render as a full green bar).
        const pct = quotaBytes > 0 ? Math.max(0, Math.min(100, (usedBytes / quotaBytes) * 100)) : 0;
        const fillClass = pct >= 90 ? "admin-usage-fill admin-usage-fill-full" : "admin-usage-fill";

        const fullName = `${u.first_name || ""} ${u.last_name || ""}`.trim();
        const nameHtml = fullName
            ? `<div class="admin-fullname">${escapeHtml(fullName)}</div>`
            : `<div class="admin-fullname admin-noname">—</div>`;

        // Left column (name / username / date + chips), right column (bar /
        // quota stats / file count) — the CSS grid aligns the three rows.
        row.innerHTML = `<span class="admin-select-dot" aria-hidden="true"></span>`
            + nameHtml
            + `<div class="admin-username">${escapeHtml(u.username)}</div>`
            + `<div class="admin-user-meta">`
            + `<span class="admin-date">${formatAdminDate(u.created)}</span>`
            + `<span class="admin-user-chips">`
            + `<span class="admin-tag admin-tag-${u.role}">${u.role}</span>`
            + `<span class="admin-tag admin-tag-${u.status}">${u.status}</span>`
            + `</span>`
            + `</div>`
            + `<div class="admin-usage-track"><div class="${fillClass}"></div></div>`
            + `<div class="admin-usage-text">${formatFileSize(usedBytes)} / ${formatFileSize(quotaBytes)}</div>`
            + `<div class="admin-usage-files">${fileCount} file${fileCount === 1 ? "" : "s"}</div>`;

        // Set the fill width via the CSSOM, not an inline style attribute: the
        // page's CSP (style-src 'self') blocks inline styles, so `style="..."`
        // in the markup is dropped and the bar would render empty or full.
        row.querySelector(".admin-usage-fill").style.width = `${pct}%`;

        row.addEventListener("click", () => selectAdminUser(u.user_id));
        row.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                selectAdminUser(u.user_id);
            }
        });
        list.appendChild(row);
    }
    updateAdminToolbar();
}

// Toolbar actions operate on the currently selected user.
function adminToggleStatusSelected() {
    const u = selectedAdminUser();
    if (!u) return;
    adminSetStatus(u.user_id, u.status === "active" ? "disabled" : "active");
}

function adminToggleRoleSelected() {
    const u = selectedAdminUser();
    if (!u) return;
    adminSetRole(u.user_id, u.role === "admin" ? "user" : "admin");
}

function adminResetPasswordSelected() {
    const u = selectedAdminUser();
    if (!u) return;
    showResetPasswordForm(u.user_id, u.username);
}

function adminSetQuotaSelected() {
    const u = selectedAdminUser();
    if (!u) return;
    showQuotaForm(u.user_id, u.username, u.quota_bytes);
}

// Quota unit conversion between the byte value stored server-side and the
// value + unit shown in the admin forms.
const QUOTA_UNIT_BYTES = {MB: 1024 ** 2, GB: 1024 ** 3, TB: 1024 ** 4};

function quotaUnitToBytes(value, unit) {
    return Math.round(value * (QUOTA_UNIT_BYTES[unit] || QUOTA_UNIT_BYTES.GB));
}

// Pick the largest unit that represents the byte count as a whole number
// (falling back to a rounded MB), so a stored quota pre-fills sensibly.
function bytesToQuotaUnit(bytes) {
    for (const unit of ["TB", "GB"]) {
        const factor = QUOTA_UNIT_BYTES[unit];
        if (bytes >= factor && bytes % factor === 0) {
            return {value: bytes / factor, unit};
        }
    }
    return {value: Math.round((bytes / QUOTA_UNIT_BYTES.MB) * 100) / 100, unit: "MB"};
}

function quotaUnitOptions(selected) {
    return ["MB", "GB", "TB"]
        .map((u) => `<option value="${u}"${u === selected ? " selected" : ""}>${u}</option>`)
        .join("");
}

function quotaInputMarkup(inputId, unitId, value, unit) {
    return `<div class="admin-quota-input">`
        + `<input type="number" id="${inputId}" placeholder="Quota" min="0" step="any" value="${value}">`
        + `<select id="${unitId}">${quotaUnitOptions(unit)}</select>`
        + `</div>`;
}

// In-app confirmation dialog (replaces window.confirm) for destructive actions.
function confirmDialog(message, confirmLabel) {
    return new Promise((resolve) => {
        const overlay = document.createElement("div");
        overlay.className = "admin-confirm-overlay";

        const modal = document.createElement("div");
        modal.className = "admin-confirm-modal";

        const msg = document.createElement("p");
        msg.className = "admin-confirm-msg";
        msg.textContent = message;

        const buttons = document.createElement("div");
        buttons.className = "admin-confirm-buttons";

        const okBtn = document.createElement("button");
        okBtn.className = "admin-confirm-ok";
        okBtn.textContent = confirmLabel || "Delete";

        const cancelBtn = document.createElement("button");
        cancelBtn.className = "admin-confirm-cancel";
        cancelBtn.textContent = "Cancel";

        buttons.appendChild(okBtn);
        buttons.appendChild(cancelBtn);
        modal.appendChild(msg);
        modal.appendChild(buttons);
        overlay.appendChild(modal);
        document.body.appendChild(overlay);
        cancelBtn.focus();

        function done(result) {
            overlay.remove();
            resolve(result);
        }
        okBtn.addEventListener("click", () => done(true));
        cancelBtn.addEventListener("click", () => done(false));
        overlay.addEventListener("click", (e) => {
            if (e.target === overlay) done(false);
        });
    });
}

function adminDeleteFilesSelected() {
    const u = selectedAdminUser();
    if (!u) return;
    adminDeleteFiles(u.user_id, u.username);
}

function adminDeleteUserSelected() {
    const u = selectedAdminUser();
    if (!u) return;
    adminDeleteUser(u.user_id, u.username);
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

// The server stores `created` as "YYYY-MM-DD HH:MM:SS"; show it as DD/MM/YYYY.
// String-split (not Date) to avoid any timezone shift on the calendar day.
function formatAdminDate(created) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(created || "");
    return m ? `${m[3]}/${m[2]}/${m[1]}` : "";
}

function showAddUserForm() {
    const form = document.querySelector("#admin-form");
    openAdminForm();
    form.innerHTML = `
        <h3>Add User</h3>
        <div class="admin-form-body">
            <div class="admin-name-row">
                <input type="text" id="admin-new-first-name" placeholder="First name (optional)" maxlength="50">
                <input type="text" id="admin-new-last-name" placeholder="Last name (optional)" maxlength="50">
            </div>
            <input type="text" id="admin-new-username" placeholder="Username" maxlength="25">
            <input type="password" id="admin-new-password" placeholder="Password" maxlength="72">
            ${currentUser && currentUser.role === "superadmin" ? `
            <select id="admin-new-role">
                <option value="user" selected>User</option>
                <option value="admin">Admin</option>
            </select>` : ""}
            ${quotaInputMarkup("admin-new-quota", "admin-new-quota-unit", 10, "GB")}
            <div id="admin-form-msg"></div>
        </div>
        <div class="admin-form-buttons">
            <button id="admin-form-submit">Create</button>
            <button id="admin-form-cancel">Cancel</button>
        </div>
    `;
    form.querySelector("#admin-form-cancel").onclick = closeAdminForm;
    form.querySelector("#admin-form-submit").onclick = async () => {
        const msg = form.querySelector("#admin-form-msg");
        msg.textContent = "";
        const quotaValue = parseFloat(form.querySelector("#admin-new-quota").value);
        if (!(quotaValue > 0)) {
            msg.textContent = "Quota must be greater than 0";
            return;
        }
        const quotaUnit = form.querySelector("#admin-new-quota-unit").value;
        // The role select is only rendered for superadmins; admins always
        // create regular users (the server enforces this too).
        const roleEl = form.querySelector("#admin-new-role");
        try {
            const res = await fetchProtected("/admin/users", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    username: form.querySelector("#admin-new-username").value,
                    password: form.querySelector("#admin-new-password").value,
                    role: roleEl ? roleEl.value : "user",
                    quota_bytes: quotaUnitToBytes(quotaValue, quotaUnit),
                    first_name: form.querySelector("#admin-new-first-name").value,
                    last_name: form.querySelector("#admin-new-last-name").value,
                }),
            });
            if (!res) return;
            closeAdminForm();
            loadAdminUsers();
        } catch (err) {
            msg.textContent = err.message || "Failed to create user";
        }
    };
}

function showResetPasswordForm(userId, username) {
    const form = document.querySelector("#admin-form");
    openAdminForm();
    form.innerHTML = `
        <h3>Reset Password for ${escapeHtml(username)}</h3>
        <div class="admin-form-body">
            <input type="password" id="admin-reset-password" placeholder="New password" maxlength="72">
            <div id="admin-form-msg"></div>
        </div>
        <div class="admin-form-buttons">
            <button id="admin-form-submit">Reset</button>
            <button id="admin-form-cancel">Cancel</button>
        </div>
    `;
    form.querySelector("#admin-form-cancel").onclick = closeAdminForm;
    form.querySelector("#admin-form-submit").onclick = async () => {
        const msg = form.querySelector("#admin-form-msg");
        msg.textContent = "";
        try {
            const res = await fetchProtected(`/admin/users/${userId}/password`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    password: form.querySelector("#admin-reset-password").value,
                }),
            });
            if (!res) return;
            closeAdminForm();
            loadAdminUsers();
        } catch (err) {
            msg.textContent = err.message || "Failed to reset password";
        }
    };
}

function showQuotaForm(userId, username, currentQuotaBytes) {
    const form = document.querySelector("#admin-form");
    openAdminForm();
    const {value, unit} = bytesToQuotaUnit(currentQuotaBytes);
    form.innerHTML = `
        <h3>Set Quota for ${escapeHtml(username)}</h3>
        <div class="admin-form-body">
            ${quotaInputMarkup("admin-quota-value", "admin-quota-unit", value, unit)}
            <div id="admin-form-msg"></div>
        </div>
        <div class="admin-form-buttons">
            <button id="admin-form-submit">Set</button>
            <button id="admin-form-cancel">Cancel</button>
        </div>
    `;
    form.querySelector("#admin-form-cancel").onclick = closeAdminForm;
    form.querySelector("#admin-form-submit").onclick = async () => {
        const msg = form.querySelector("#admin-form-msg");
        msg.textContent = "";
        const quotaValue = parseFloat(form.querySelector("#admin-quota-value").value);
        if (!(quotaValue > 0)) {
            msg.textContent = "Quota must be greater than 0";
            return;
        }
        const quotaUnit = form.querySelector("#admin-quota-unit").value;
        try {
            const res = await fetchProtected(`/admin/users/${userId}/quota`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    quota_bytes: quotaUnitToBytes(quotaValue, quotaUnit),
                }),
            });
            if (!res) return;
            closeAdminForm();
            loadAdminUsers();
        } catch (err) {
            msg.textContent = err.message || "Failed to set quota";
        }
    };
}

async function adminSetStatus(userId, status) {
    try {
        const res = await fetchProtected(`/admin/users/${userId}/status`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({status}),
        });
        if (!res) return;
        loadAdminUsers();
    } catch (err) {
        showAdminError(err.message || "Failed to update status");
    }
}

async function adminSetRole(userId, role) {
    try {
        const res = await fetchProtected(`/admin/users/${userId}/role`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({role}),
        });
        if (!res) return;
        loadAdminUsers();
    } catch (err) {
        showAdminError(err.message || "Failed to update role");
    }
}

async function adminDeleteFiles(userId, username) {
    const ok = await confirmDialog(`Delete ALL files owned by "${username}"? The account is kept.`, "Delete files");
    if (!ok) return;
    try {
        const res = await fetchProtected(`/admin/users/${userId}/files`, {
            method: "DELETE",
        });
        if (!res) return;
        loadAdminUsers();
    } catch (err) {
        showAdminError(err.message || "Failed to delete files");
    }
}

async function adminDeleteUser(userId, username) {
    const ok = await confirmDialog(`Delete user "${username}"? This removes the account and all their files.`, "Delete user");
    if (!ok) return;
    try {
        const res = await fetchProtected(`/admin/users/${userId}`, {
            method: "DELETE",
        });
        if (!res) return;
        loadAdminUsers();
    } catch (err) {
        showAdminError(err.message || "Failed to delete user");
    }
}


// --- Page initialization ---

window.addEventListener("load", () => {
    initUplivion();
});

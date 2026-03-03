document.addEventListener("DOMContentLoaded", () => {
    const overlay = document.createElement("div");
    overlay.className = "ai-loader-overlay";
    overlay.innerHTML = `
        <div class="ai-loader-card" role="status" aria-live="polite">
            <div class="loader-skeleton">
                <span></span><span></span><span></span>
            </div>
            <p id="loader-status">Analyzing topic...</p>
        </div>
    `;
    document.body.appendChild(overlay);

    const status = overlay.querySelector("#loader-status");
    const messages = [
        "Analyzing topic...",
        "Structuring notes...",
        "Preparing exam questions...",
        "Finalizing output..."
    ];
    let ticker = null;

    const showLoader = () => {
        overlay.classList.add("is-active");
        let index = 0;
        status.textContent = messages[index];
        ticker = window.setInterval(() => {
            index = (index + 1) % messages.length;
            status.textContent = messages[index];
        }, 1400);
    };

    const stopLoader = () => {
        if (ticker) {
            window.clearInterval(ticker);
            ticker = null;
        }
    };

    document.querySelectorAll("form").forEach((form) => {
        form.addEventListener("submit", () => {
            showLoader();
        });
    });

    window.addEventListener("pageshow", stopLoader);
});

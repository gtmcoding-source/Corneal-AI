document.addEventListener("DOMContentLoaded", () => {
    const body = document.body;
    if (!body) return;

    const themeToggle = document.getElementById("theme-toggle");
    const savedTheme = window.localStorage.getItem("corneal-theme");
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const initialTheme = savedTheme || (prefersDark ? "dark" : "light");
    body.setAttribute("data-theme", initialTheme);
    if (themeToggle) {
        themeToggle.textContent = initialTheme === "dark" ? "Light Mode" : "Dark Mode";
        themeToggle.addEventListener("click", () => {
            const current = body.getAttribute("data-theme") || "light";
            const next = current === "dark" ? "light" : "dark";
            body.setAttribute("data-theme", next);
            window.localStorage.setItem("corneal-theme", next);
            themeToggle.textContent = next === "dark" ? "Light Mode" : "Dark Mode";
        });
    }

    body.classList.add("motion-enabled");

    const revealTargets = document.querySelectorAll(
        ".panel, .journey-card, .metric-item, .feature-card, .quote-card, .dashboard-card, .plan-box, .step-card, .dashboard-kpi-card"
    );
    revealTargets.forEach((el) => el.setAttribute("data-reveal", "true"));

    const observer = new IntersectionObserver(
        (entries) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) {
                    entry.target.classList.add("is-visible");
                    observer.unobserve(entry.target);
                }
            });
        },
        { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
    );
    revealTargets.forEach((el) => observer.observe(el));

    document.querySelectorAll("form").forEach((form) => {
        form.addEventListener("submit", () => {
            const submitBtn = form.querySelector("button[type='submit'], button:not([type])");
            if (!submitBtn) return;
            submitBtn.disabled = true;
            submitBtn.dataset.originalText = submitBtn.textContent || "Submit";
            submitBtn.textContent = "Processing...";
        });
    });

    const sectionIcon = (title) => {
        const normalized = (title || "").toLowerCase();
        if (normalized.includes("definition")) return "DEF";
        if (normalized.includes("key concept")) return "KEY";
        if (normalized.includes("formula")) return "FRM";
        if (normalized.includes("mistake")) return "ERR";
        if (normalized.includes("question")) return "Q";
        if (normalized.includes("revision")) return "REV";
        return "SEC";
    };

    document.querySelectorAll(".note-collapsible").forEach((container) => {
        const nodes = Array.from(container.childNodes);
        const sections = [];
        let current = null;

        nodes.forEach((node) => {
            if (node.nodeType === Node.ELEMENT_NODE && node.tagName === "H2") {
                if (current) sections.push(current);
                current = { heading: node.textContent.trim(), nodes: [] };
            } else if (current) {
                current.nodes.push(node);
            }
        });
        if (current) sections.push(current);
        if (!sections.length) return;

        container.innerHTML = "";
        sections.forEach((section, idx) => {
            const card = document.createElement("details");
            card.className = "note-section-card";
            card.open = idx === 0;

            const summary = document.createElement("summary");
            summary.innerHTML = `<span class="note-section-icon">${sectionIcon(section.heading)}</span><span>${section.heading}</span>`;
            card.appendChild(summary);

            const bodyWrap = document.createElement("div");
            bodyWrap.className = "note-section-body";
            section.nodes.forEach((node) => bodyWrap.appendChild(node));
            card.appendChild(bodyWrap);
            container.appendChild(card);
        });
    });

    const currentPath = window.location.pathname;
    document.querySelectorAll(".sidebar-link").forEach((link) => {
        const href = link.getAttribute("href") || "";
        if (href.startsWith("#")) return;
        if (href === currentPath || (href !== "/" && currentPath.startsWith(href))) {
            link.classList.add("sidebar-link-active");
        }
    });

    const toastNode = document.getElementById("toast");
    const toastMessage = body.dataset.toast;
    if (toastNode && toastMessage) {
        toastNode.textContent = toastMessage;
        toastNode.classList.add("toast-show");
        window.setTimeout(() => toastNode.classList.remove("toast-show"), 2800);
    }

    if (typeof window.renderMermaidMindmaps === "function") {
        window.renderMermaidMindmaps();
    }
});

document.addEventListener("DOMContentLoaded", () => {
    const body = document.body;
    if (!body) return;

    body.classList.add("motion-enabled");

    const revealTargets = document.querySelectorAll(
        ".panel, .journey-card, .metric-item, .feature-card, .quote-card, .dashboard-card, .plan-box, .step-card"
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
});

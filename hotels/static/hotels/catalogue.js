(() => {
    "use strict";
    const catalogue = document.querySelector("[data-async-catalogue]");
    if (!catalogue || !window.fetch || !window.AbortController) return;
    const content = catalogue.querySelector("[data-catalogue-content]");
    const feedback = catalogue.querySelector("[data-catalogue-feedback]");
    const retry = catalogue.querySelector("[data-catalogue-retry]");
    let pending;
    let lastUrl;
    let lastPush;

    async function load(url, push = true) {
        if (pending) pending.abort();
        const controller = new AbortController();
        pending = controller;
        lastUrl = url;
        lastPush = push;
        catalogue.setAttribute("aria-busy", "true");
        feedback.hidden = false;
        feedback.classList.remove("request-error");
        feedback.textContent = "Loading results…";
        retry.hidden = true;
        try {
            const response = await fetch(url, {
                headers: {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
                credentials: "same-origin",
                signal: controller.signal,
            });
            if (controller !== pending) return;
            if (response.redirected) {
                window.location.assign(response.url);
                return;
            }
            if (!response.ok) throw new Error("Request failed");
            const data = await response.json();
            if (controller !== pending) return;
            if (typeof data.html !== "string" || typeof data.count !== "number") {
                throw new Error("Invalid response");
            }
            content.innerHTML = data.html;
            if (push && url !== window.location.href) history.pushState(null, "", url);
            feedback.textContent = data.valid
                ? `${data.count} ${data.count === 1 ? "result" : "results"} found. Page ${data.page}.`
                : "Correct the filters below to see matching results.";
            const focusTarget = content.querySelector("[aria-invalid='true']") || content.querySelector("h1");
            if (focusTarget) {
                if (focusTarget.tagName === "H1") focusTarget.setAttribute("tabindex", "-1");
                focusTarget.focus({preventScroll: true});
            }
        } catch (error) {
            if (controller !== pending || error.name === "AbortError") return;
            feedback.classList.add("request-error");
            feedback.textContent = "Results could not be updated. Previous results are still shown. Try again or reload the page.";
            retry.hidden = false;
        } finally {
            if (controller === pending) catalogue.setAttribute("aria-busy", "false");
        }
    }

    catalogue.addEventListener("submit", (event) => {
        const form = event.target;
        if (!form.matches("form.filters")) return;
        event.preventDefault();
        const url = new URL(form.action);
        url.search = new URLSearchParams(new FormData(form)).toString();
        load(url.href);
    });

    catalogue.addEventListener("click", (event) => {
        const link = event.target.closest(".pagination a, [data-catalogue-reset]");
        if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        load(link.href);
    });

    retry.addEventListener("click", () => load(lastUrl, lastPush));
    window.addEventListener("popstate", () => load(window.location.href, false));
})();

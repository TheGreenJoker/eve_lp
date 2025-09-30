document.addEventListener("DOMContentLoaded", function() {
    const form = document.getElementById("lpForm");
    const loading = document.getElementById("loading");

    form.addEventListener("submit", function() {
        loading.style.display = "block";
    });
});

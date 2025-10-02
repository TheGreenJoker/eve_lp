document.addEventListener("DOMContentLoaded", function() {
    const form = document.getElementById("lpForm");
    const loading = document.getElementById("loading");
    if (form) {
      form.addEventListener("submit", function() {
        loading.classList.remove("hidden");
      });
    }
  
    // Toggle affichage compact / exact au clic sur les nombres
    document.querySelectorAll(".toggle-number").forEach(span => {
      span.addEventListener("click", () => {
        const exact = span.getAttribute("data-exact");
        const current = span.textContent.trim();
  
        // Si affichage compact, on affiche exact, sinon on affiche compact
        if (current === exact) {
          // Repasser en compact
          span.textContent = formatCompact(Number(exact));
        } else {
          // Afficher exact
          span.textContent = exact;
        }
      });
    });
  
    // Fonction JS pour format compact (même logique que Python)
    function formatCompact(n) {
      const absN = Math.abs(n);
      if (absN >= 1_000_000_000) {
        return (n / 1_000_000_000).toFixed(1) + "B";
      } else if (absN >= 1_000_000) {
        return (n / 1_000_000).toFixed(1) + "M";
      } else if (absN >= 1_000) {
        return (n / 1_000).toFixed(1) + "k";
      } else {
        return n.toFixed(0);
      }
    }
  });
  
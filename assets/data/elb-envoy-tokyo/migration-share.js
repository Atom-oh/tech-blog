(() => {
  const chart = document.getElementById("hybrid-migration-share");
  if (!chart) return;

  const input = chart.querySelector("input");
  const fixed = Number(chart.dataset.fixedPods);
  const number = new Intl.NumberFormat("ko-KR", {maximumFractionDigits: 1});
  function render() {
    const pods = Number(input.value);
    const eks = 100 * pods / (fixed + pods);
    const onPrem = 100 - eks;
    chart.querySelector("[data-eks-count]").textContent = pods;
    chart.querySelector("[data-eks-share]").textContent = number.format(eks) + "%";
    chart.querySelector("[data-fixed-share]").textContent = number.format(onPrem) + "%";
    chart.querySelector("[data-fixed-bar]").setAttribute("width", onPrem);
    chart.querySelector("[data-eks-bar]").setAttribute("x", onPrem);
    chart.querySelector("[data-eks-bar]").setAttribute("width", eks);
    input.setAttribute("aria-valuetext", pods + "개, EKS 기대 비중 " + number.format(eks) + "%");
  }
  input.addEventListener("input", render);
  render();
  chart.querySelector(".pod-selector").hidden = false;
})();

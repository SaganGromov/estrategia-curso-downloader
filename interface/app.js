const token = new URLSearchParams(window.location.search).get("token") || "";
const api = (path, options = {}) => fetch(`${path}?token=${encodeURIComponent(token)}`, {
  cache: "no-store",
  headers: { "Content-Type": "application/json", "X-Interface-Token": token, ...(options.headers || {}) },
  ...options,
});

const $ = (id) => document.getElementById(id);
let lastLogText = "";
let currentStatus = "configuracao";

async function action(path, body = {}) {
  const response = await api(path, { method: "POST", body: JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.erro || "Não foi possível concluir a ação.");
  return data;
}

function percent(value) {
  const number = Number(value || 0);
  return `${number.toFixed(number >= 10 ? 0 : 1)}%`;
}

function setProgress(id, value) {
  $(id).style.width = `${Math.max(0, Math.min(100, Number(value || 0)))}%`;
}

function render(state) {
  currentStatus = state.status;
  $("modeBadge").textContent = state.modo;
  $("modeDashboard").textContent = state.modo;
  if (!$('email').value) $('email').value = state.email_inicial || "";
  if (!$('course').value) $('course').value = state.curso_inicial || "";
  if (state.pasta_base) $("folderPath").textContent = state.pasta_base;

  const configuring = state.status === "configuracao";
  $("setup").classList.toggle("hidden", !configuring);
  $("dashboard").classList.toggle("hidden", configuring);
  if (configuring) return;

  $("phase").textContent = state.fase;
  $("found").textContent = state.encontrados;
  $("downloaded").textContent = state.baixados;
  $("failed").textContent = state.falhas;
  $("lesson").textContent = `${state.aula_atual} / ${state.total_aulas}`;
  $("destination").textContent = state.pasta_destino || "A pasta será criada após o login";
  $("openFolder").disabled = !state.pasta_destino;

  const item = state.item;
  $("itemName").textContent = item.nome;
  $("itemPercent").textContent = percent(item.percentual);
  $("itemSize").textContent = `${item.recebido} / ${item.total}`;
  $("itemSpeed").textContent = item.velocidade;
  $("itemEta").textContent = item.eta;
  setProgress("itemBar", item.percentual);

  const total = state.total;
  $("totalPercent").textContent = percent(total.percentual);
  $("totalSize").textContent = `${total.pronto} / ${total.conhecido}`;
  $("totalSpeed").textContent = total.velocidade;
  $("knownEta").textContent = total.eta;
  $("courseEta").textContent = total.curso_eta;
  setProgress("totalBar", total.percentual);

  const logText = state.logs.join("\n");
  if (logText !== lastLogText) {
    $("logs").textContent = logText || "Aguardando atividade…";
    $("logs").scrollTop = $("logs").scrollHeight;
    lastLogText = logText;
  }
  $("dashboardError").textContent = state.erro || "";

  const terminal = ["concluido", "erro", "cancelado"].includes(state.status);
  $("cancelButton").classList.toggle("hidden", terminal);
  $("shutdownButton").classList.toggle("hidden", !terminal);
  $("statusDot").classList.toggle("done", state.status === "concluido");
  $("statusDot").classList.toggle("error", ["erro", "cancelado"].includes(state.status));
}

async function poll() {
  try {
    const response = await api("/api/state");
    if (!response.ok) throw new Error();
    render(await response.json());
    $("connection").classList.remove("offline");
    $("connection").lastChild.textContent = " Conectado";
  } catch (_) {
    $("connection").classList.add("offline");
    $("connection").lastChild.textContent = " Desconectado";
  }
}

$("togglePassword").addEventListener("click", () => {
  const input = $("password");
  input.type = input.type === "password" ? "text" : "password";
  $("togglePassword").textContent = input.type === "password" ? "Mostrar" : "Ocultar";
});

$("selectFolder").addEventListener("click", async () => {
  $("selectFolder").disabled = true;
  $("formError").textContent = "Abrindo o seletor de pastas…";
  try {
    const data = await action("/api/select-folder");
    if (data.pasta) $("folderPath").textContent = data.pasta;
    $("formError").textContent = "";
  } catch (error) { $("formError").textContent = error.message; }
  finally { $("selectFolder").disabled = false; }
});

$("startForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("startButton").disabled = true;
  $("formError").textContent = "Validando dados…";
  try {
    await action("/api/start", { email: $("email").value, senha: $("password").value, curso: $("course").value });
    $("password").value = "";
    await poll();
  } catch (error) {
    $("formError").textContent = error.message;
    $("startButton").disabled = false;
  }
});

$("cancelButton").addEventListener("click", async () => {
  if (!confirm("Cancelar o download atual? Arquivos incompletos permanecerão com extensão .part.")) return;
  try { await action("/api/cancel"); } catch (error) { $("dashboardError").textContent = error.message; }
});
$("openFolder").addEventListener("click", () => action("/api/open-folder").catch((error) => { $("dashboardError").textContent = error.message; }));
$("shutdownButton").addEventListener("click", async () => { await action("/api/shutdown"); window.close(); });
$("closeSetup").addEventListener("click", async () => { await action("/api/shutdown"); window.close(); });

poll();
setInterval(poll, 700);

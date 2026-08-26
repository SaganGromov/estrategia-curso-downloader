const token = new URLSearchParams(window.location.search).get("token") || "";
const api = (path, options = {}) => fetch(`${path}?token=${encodeURIComponent(token)}`, {
  cache: "no-store",
  headers: { "Content-Type": "application/json", "X-Interface-Token": token, "X-Estrategia-Request": "1", ...(options.headers || {}) },
  ...options,
});

const $ = (id) => document.getElementById(id);
let lastLogText = "";
let currentStatus = "configuracao";
let setupInitialized = false;

function selectedMode() {
  return document.querySelector("input[name='mode']:checked")?.value || "completo";
}

function updateCourseRequirement() {
  const integral = selectedMode() === "integral";
  $("course").disabled = integral;
  $("course").required = !integral;
  $("courseField").classList.toggle("disabled-field", integral);
  if (integral) {
    $("courseFeedback").textContent = "O catálogo completo será obtido após o login.";
    $("courseFeedback").className = "field-feedback valid";
  } else {
    validateCourse();
  }
}

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
  $("freeSpace").textContent = state.espaco_disponivel ? `Espaço disponível: ${state.espaco_disponivel}` : "";
  if (!setupInitialized) {
    const mode = state.modo_integral ? "integral" : (state.modo_reduzido ? "reduzido" : "completo");
    document.querySelector(`input[name='mode'][value='${mode}']`).checked = true;
    updateCourseRequirement();
    setupInitialized = true;
  }

  const configuring = state.status === "configuracao";
  $("setup").classList.toggle("hidden", !configuring);
  $("dashboard").classList.toggle("hidden", configuring);
  if (configuring) return;

  $("phase").textContent = state.fase;
  $("phaseInstruction").textContent = state.instrucao_login || "";
  $("found").textContent = state.encontrados;
  $("downloaded").textContent = state.baixados;
  $("existing").textContent = state.existentes;
  $("failed").textContent = state.falhas;
  $("lesson").textContent = `${state.aula_atual} / ${state.total_aulas}`;
  $("courseProgress").textContent = `${state.curso_atual || 0} / ${state.total_cursos || 0}`;
  $("destination").textContent = state.pasta_destino || "A pasta será preparada após o login";
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
  $("warning").textContent = state.aviso || "";
  $("warning").classList.toggle("hidden", !state.aviso);

  const terminal = ["concluido", "erro", "cancelado"].includes(state.status);
  $("cancelButton").classList.toggle("hidden", terminal);
  $("shutdownButton").classList.toggle("hidden", !terminal);
  $("copyDiagnostic").classList.toggle("hidden", !state.diagnostico_disponivel);
  $("summaryCard").classList.toggle("hidden", !terminal);
  const summary = state.resumo || {};
  $("summaryFound").textContent = summary.encontrados ?? state.encontrados;
  $("summaryDownloaded").textContent = summary.baixados ?? state.baixados;
  $("summaryExisting").textContent = summary.existentes ?? state.existentes;
  $("summaryFailed").textContent = summary.falhas ?? state.falhas;
  $("summaryBytes").textContent = summary.volume ?? "0 B";
  $("summaryElapsed").textContent = summary.tempo ?? "--:--";
  $("statusDot").classList.toggle("done", state.status === "concluido");
  $("statusDot").classList.toggle("error", ["erro", "cancelado"].includes(state.status));
}

function validateCourse() {
  if (selectedMode() === "integral") return true;
  const value = $("course").value.trim();
  const match = value.match(/^\d+$/) || value.match(/\/cursos\/(\d+)(?=[/?#]|$)/);
  const id = match ? (match[1] || match[0]) : "";
  $("courseFeedback").textContent = id ? `Curso identificado: ${id}` : (value ? "Informe um ID numérico ou uma URL válida do curso." : "");
  $("courseFeedback").className = `field-feedback ${id ? "valid" : (value ? "invalid" : "")}`;
  return Boolean(id);
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
$("course").addEventListener("input", validateCourse);
$("course").addEventListener("blur", validateCourse);
document.querySelectorAll("input[name='mode']").forEach((input) => {
  input.addEventListener("change", updateCourseRequirement);
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
    if (!validateCourse()) throw new Error("Corrija o ID ou a URL do curso.");
    const mode = selectedMode();
    await action("/api/start", { email: $("email").value, senha: $("password").value, curso: $("course").value, modo: mode });
    $("password").value = "";
    await poll();
  } catch (error) {
    $("formError").textContent = error.message;
    $("startButton").disabled = false;
  }
});

$("cancelButton").addEventListener("click", async () => {
  if (!confirm("Cancelar o download atual? Arquivos incompletos permanecerão com extensão .part.")) return;
  $("cancelButton").disabled = true;
  $("cancelButton").textContent = "Cancelando…";
  try { await action("/api/cancel"); } catch (error) { $("dashboardError").textContent = error.message; $("cancelButton").disabled = false; }
});
$("openFolder").addEventListener("click", () => action("/api/open-folder").catch((error) => { $("dashboardError").textContent = error.message; }));
$("toggleDetails").addEventListener("click", () => {
  $("logs").classList.toggle("hidden");
  $("toggleDetails").textContent = $("logs").classList.contains("hidden") ? "Ver detalhes" : "Ocultar detalhes";
});
$("copyDiagnostic").addEventListener("click", async () => {
  try {
    const response = await api("/api/diagnostic");
    const data = await response.json();
    if (!response.ok) throw new Error(data.erro);
    await navigator.clipboard.writeText(data.diagnostico);
    $("copyDiagnostic").textContent = "Diagnóstico copiado";
  } catch (error) { $("dashboardError").textContent = `Não foi possível copiar: ${error.message}`; }
});
$("shutdownButton").addEventListener("click", async () => { await action("/api/shutdown"); window.close(); });
$("closeSetup").addEventListener("click", async () => { await action("/api/shutdown"); window.close(); });

poll();
setInterval(poll, 700);

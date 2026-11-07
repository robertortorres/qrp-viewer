(() => {
  "use strict";
  const csrf = document.body.dataset.csrf;

  // Confirmação antes de ações destrutivas
  document.querySelectorAll("form[data-confirm]").forEach((f) => {
    f.addEventListener("submit", (ev) => {
      if (!confirm(f.dataset.confirm)) ev.preventDefault();
    });
  });

  // Imprimir o PDF exibido na página
  document.querySelectorAll("[data-print-frame]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const fr = document.getElementById(btn.dataset.printFrame);
      try { fr.contentWindow.focus(); fr.contentWindow.print(); }
      catch (e) { window.open(fr.src, "_blank"); }
    });
  });

  // Imprimir um PDF sem abrir a página do exame (iframe oculto)
  document.querySelectorAll("[data-print]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("iframe.print-helper").forEach((x) => x.remove());
      const fr = document.createElement("iframe");
      fr.className = "print-helper";
      fr.style.cssText = "position:fixed;right:0;bottom:0;width:1px;height:1px;border:0;opacity:0";
      fr.src = btn.dataset.print;
      fr.addEventListener("load", () => {
        setTimeout(() => {
          try { fr.contentWindow.focus(); fr.contentWindow.print(); }
          catch (e) { window.open(btn.dataset.print, "_blank"); }
        }, 300);
      });
      document.body.appendChild(fr);
    });
  });

  // Importação: um arquivo por requisição, com resultado de cada um
  const form = document.getElementById("uploadform");
  if (!form) return;
  const input = document.getElementById("files");
  const list = document.getElementById("results");

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  const renderResult = (li, r) => {
    li.className = `result result-${r.status}`;
    li.replaceChildren();
    li.append(el("span", "result-file", r.file));
    const msg = el("span", "result-msg");
    if (r.patient) {
      const a = el("a", null, r.patient);
      a.href = r.patient_url || r.url;
      msg.append(a);
      let extra = "";
      if (r.eye) extra += `, ${r.eye.toLowerCase()}`;
      if (r.date) extra += `, ${r.date}`;
      msg.append(document.createTextNode(`${extra}. `));
    }
    msg.append(document.createTextNode(r.message));
    li.append(msg);
    if (r.url) {
      const b = el("a", "btn btn-small", "Ver exame");
      b.href = r.url;
      li.append(b);
    }
  };

  const send = async (file) => {
    const li = el("li", "result result-pending");
    li.append(el("span", "result-file", file.name), el("span", "result-msg", "Importando…"));
    list.prepend(li);
    const fd = new FormData();
    fd.append("files", file);
    try {
      const resp = await fetch(form.action, {
        method: "POST", body: fd, credentials: "same-origin",
        headers: { "X-CSRF-Token": csrf, Accept: "application/json" },
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail || `Erro ${resp.status}`);
      renderResult(li, data.results[0]);
    } catch (e) {
      renderResult(li, { file: file.name, status: "error", message: e.message });
    }
  };

  const handle = async (files) => {
    if (!files.length) return;
    form.classList.add("is-busy");
    for (const f of files) await send(f);
    form.classList.remove("is-busy");
    input.value = "";
  };

  input.addEventListener("change", () => handle([...input.files]));
  ["dragenter", "dragover"].forEach((t) => form.addEventListener(t, (e) => {
    e.preventDefault(); form.classList.add("is-over");
  }));
  ["dragleave", "drop"].forEach((t) => form.addEventListener(t, (e) => {
    e.preventDefault(); form.classList.remove("is-over");
  }));
  form.addEventListener("drop", (e) => handle([...e.dataTransfer.files]));
  form.addEventListener("submit", (e) => { e.preventDefault(); handle([...input.files]); });
})();

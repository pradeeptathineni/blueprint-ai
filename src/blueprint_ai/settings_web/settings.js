"use strict";
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="settings-token"]').content;
let saved,
  category = "",
  updates = {},
  resets = new Set(),
  reviewed = false,
  busy = false,
  uncertain = false;
const node = (tag, text) => {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  return e;
};
const status = (text) => {
  $("status").textContent = text;
};
const display = (value) =>
  value === null
    ? "Disabled"
    : Array.isArray(value)
      ? JSON.stringify(value)
      : String(value);
async function api(body) {
  const response = await fetch(
    "/api/settings",
    body === undefined
      ? { cache: "no-store" }
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Settings-Token": token,
          },
          body: JSON.stringify(body),
          cache: "no-store",
        },
  );
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Settings request failed.");
  return result;
}
function controls() {
  const dirty = Object.keys(updates).length + resets.size > 0;
  $("preview").disabled = busy || uncertain || !dirty;
  $("save").disabled = busy || uncertain || !reviewed;
  $("reload").disabled = busy;
  for (const input of $("settings").querySelectorAll(
    "input,select,textarea,button",
  ))
    input.disabled = busy || uncertain;
}
function draft() {
  $("draft").replaceChildren();
  for (const item of saved.settings) {
    if (!(item.id in updates) && !resets.has(item.id)) continue;
    const value = resets.has(item.id) ? item.default : updates[item.id];
    $("draft").append(
      node(
        "li",
        `${item.label}: ${display(item.effective)} → ${display(value)}${resets.has(item.id) ? " (remove project override)" : ""}`,
      ),
    );
  }
  controls();
}
function categories(matches) {
  const names = ["", ...new Set(saved.settings.map((item) => item.category))];
  const nav = $("categories");
  if (nav.dataset.names !== JSON.stringify(names)) {
    nav.replaceChildren();
    for (const name of names) {
      const button = node("button");
      button.type = "button";
      button.dataset.category = name;
      button.setAttribute("aria-controls", "settings");
      button.addEventListener("click", () => {
        category = name;
        $("search").value = "";
        render();
      });
      nav.append(button);
    }
    nav.dataset.names = JSON.stringify(names);
  }
  for (const button of nav.children) {
    const name = button.dataset.category;
    const count = matches.filter(
      (item) => !name || item.category === name,
    ).length;
    button.textContent = `${name || "All settings"} (${count})`;
    button.setAttribute("aria-pressed", String(category === name));
  }
}
function render() {
  if (!saved) return;
  const query = $("search").value.toLowerCase();
  const matches = saved.settings.filter(
    (item) =>
      (!$("changed").checked || item.effective !== item.default) &&
      [item.id, item.label, item.category, item.description]
        .join(" ")
        .toLowerCase()
        .includes(query),
  );
  const entries = matches.filter(
    (item) => !category || item.category === category,
  );
  categories(matches);
  $("category-heading").textContent = category || "All settings";
  $("settings-count").textContent =
    `${entries.length} ${entries.length === 1 ? "setting" : "settings"}${query ? " matching your search" : ""}`;
  $("settings").replaceChildren();
  let group;
  for (const item of entries) {
    if (item.category !== group) {
      if (!category) $("settings").append(node("h2", item.category));
      group = item.category;
    }
    const row = node("article"),
      details = node("div"),
      inputBox = node("div");
    inputBox.className = "control";
    const label = node("label", item.label);
    label.htmlFor = "setting-" + item.id;
    details.append(
      label,
      node("p", item.description),
      node(
        "small",
        `Saved: ${display(item.effective)} · Default: ${display(item.default)} · Source: ${item.source} · Scope: ${item.scope}`,
      ),
    );
    if (item.overrides) details.append(node("small", item.overrides));
    const schema = item.constraints,
      enumValues = schema.enum || schema.anyOf?.find((s) => s.enum)?.enum;
    let input;
    if (schema.type === "array") {
      input = node("textarea");
      input.rows = 5;
      input.spellcheck = false;
      const value = resets.has(item.id)
        ? item.default
        : item.id in updates
          ? updates[item.id]
          : item.effective;
      input.value = value.join("\n");
    } else if (enumValues) {
      input = node("select");
      if (schema.anyOf?.some((s) => s.type === "null")) {
        const opt = node("option", "Disabled");
        opt.value = "null";
        input.append(opt);
      }
      for (const value of enumValues) {
        const opt = node("option", value);
        opt.value = JSON.stringify(value);
        input.append(opt);
      }
      input.value = JSON.stringify(
        resets.has(item.id)
          ? item.default
          : item.id in updates
            ? updates[item.id]
            : item.effective,
      );
    } else {
      input = node("input");
      input.type = "number";
      input.step = "1";
      input.min = schema.minimum;
      input.max = schema.maximum;
      input.value = resets.has(item.id)
        ? item.default
        : item.id in updates
          ? updates[item.id]
          : item.effective;
    }
    input.id = "setting-" + item.id;
    input.addEventListener("input", () => {
      updates[item.id] =
        schema.type === "array"
          ? input.value === ""
            ? []
            : input.value.split("\n")
          : enumValues
            ? JSON.parse(input.value)
            : Number(input.value);
      resets.delete(item.id);
      reviewed = false;
      draft();
    });
    const reset = node("button", "Use default");
    reset.disabled = item.source === "default" && !(item.id in updates);
    reset.addEventListener("click", () => {
      delete updates[item.id];
      resets.add(item.id);
      reviewed = false;
      render();
      draft();
    });
    inputBox.append(input, reset);
    row.append(details, inputBox);
    $("settings").append(row);
  }
  if (!entries.length)
    $("settings").append(node("p", "No settings match these filters."));
  controls();
}
async function reload() {
  busy = true;
  controls();
  try {
    const result = await api();
    saved = result;
    $("project").textContent = `Blueprint AI / ${saved.projectLabel}`;
    updates = {};
    resets = new Set();
    reviewed = false;
    uncertain = false;
    if (!saved.settings.some((item) => item.category === category))
      category = "";
    $("limits").replaceChildren(...saved.limitations.map((s) => node("li", s)));
    $("unmanaged").textContent = saved.unmanaged.join(", ");
    status("Saved settings loaded. Preview a change before saving.");
    render();
    draft();
  } catch (error) {
    uncertain = true;
    status("Cannot load saved settings. " + error.message);
  } finally {
    busy = false;
    controls();
  }
}
async function submit(action) {
  busy = true;
  controls();
  try {
    const result = await api({
      action,
      updates,
      resets: [...resets],
      expectedRevision: saved.revision,
    });
    if (action === "preview") {
      reviewed = true;
      status("Preview validated. Review the changes below, then save.");
    } else {
      saved = result;
      updates = {};
      resets.clear();
      reviewed = false;
      status("Saved. The next operation uses these project defaults.");
      render();
      draft();
    }
  } catch (error) {
    reviewed = false;
    if (action === "save") {
      uncertain = true;
      status(
        "Save was not confirmed. Reload saved settings before another edit. " +
          error.message,
      );
    } else status(error.message);
  } finally {
    busy = false;
    controls();
  }
}
$("preview").addEventListener("click", () => submit("preview"));
$("save").addEventListener("click", () => submit("save"));
$("reload").addEventListener("click", reload);
$("search").addEventListener("input", () => {
  category = "";
  render();
});
$("changed").addEventListener("input", render);
reload();

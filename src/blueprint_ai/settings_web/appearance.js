// Adapted from Monitoring AI appearance.js at 3080bc9268d52c7f70a54bf365a369169018aec8
// MIT notice retained in theme/LICENSE; Blueprint owns its browser-only preference key
import {
  presets,
  defaultPreference,
  parsePreference,
  resolveTheme,
  cssVariables,
} from "./theme/index.js";

const STORAGE_KEY = "blueprint-ai.appearance.v1";
const STORAGE_BLOCKED = "Browser storage is unavailable. This appearance applies only to this page.";
const root = document.documentElement;
const host = document.getElementById("appearance-controls");
const darkQuery = window.matchMedia?.("(prefers-color-scheme: dark)") || { matches: false };
const themeColorMeta = document.querySelector('meta[name="theme-color"]');
const schemeMeta = document.querySelector('meta[name="color-scheme"]');

function el(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function safeStorage() {
  try { return window.localStorage; } catch { return null; }
}

function readPreference() {
  const storage = safeStorage();
  if (!storage) return { preference: defaultPreference, note: STORAGE_BLOCKED };
  let raw;
  try { raw = storage.getItem(STORAGE_KEY); }
  catch { return { preference: defaultPreference, note: STORAGE_BLOCKED };
  }
  if (raw === null) return { preference: defaultPreference, note: "" };
  try { return { preference: parsePreference(raw), note: "" }; }
  catch (error) {
    return {
      preference: defaultPreference,
      note: `Saved appearance is invalid (${error.message}). Using the default. The saved value remains unchanged until you choose or reset an appearance.`,
    };
  }
}

const { preference: initialPreference, note: initialNote } = readPreference();
let current = initialPreference;

if (host) initializeControls();

function initializeControls() {
  const wrapper = el("div", undefined, "appearance-adapter");
  const row = el("div", undefined, "appearance-row");
  const presetLabel = el("label", undefined, "appearance-field");
  presetLabel.append(el("span", "Theme preset"));
  const presetSelect = el("select");
  presetSelect.id = "appearance-preset";
  presetSelect.setAttribute("aria-label", "Theme preset");
  const systemOption = el("option", "System"); systemOption.value = "system"; presetSelect.append(systemOption);
  for (const preset of presets) {
    const option = el("option", preset.label); option.value = preset.id; presetSelect.append(option);
  }
  presetLabel.append(presetSelect);
  row.append(presetLabel);
  wrapper.append(row);

  const fieldset = el("fieldset", undefined, "appearance-overrides");
  fieldset.append(el("legend", "Custom colors and spacing"));
  const colors = el("div", undefined, "appearance-color-grid");
  const colorInputs = {};
  const dirtyOverrides = new Set();
  for (const [key, label] of [["accent", "Accent color"], ["focus", "Focus ring color"], ["brandAction", "Action color"]]) {
    const control = el("label", undefined, "appearance-field");
    control.append(el("span", label));
    const input = el("input"); input.type = "color"; input.id = `appearance-${key.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)}`; input.dataset.appearanceOverride = key;
    input.setAttribute("aria-label", label); colorInputs[key] = input; control.append(input); colors.append(control);
    input.addEventListener("input", () => dirtyOverrides.add(key));
  }
  fieldset.append(colors);

  const optionRow = el("div", undefined, "appearance-row");
  const radiusLabel = el("label", undefined, "appearance-field"); radiusLabel.append(el("span", "Corner radius"));
  const radius = el("input"); radius.id = "appearance-radius"; radius.type = "range"; radius.min = "0"; radius.max = "20"; radius.step = "1"; radius.dataset.appearanceOverride = "radius"; radius.setAttribute("aria-label", "Corner radius");
  radiusLabel.append(radius); optionRow.append(radiusLabel);
  radius.addEventListener("input", () => dirtyOverrides.add("radius"));
  const densityLabel = el("label", undefined, "appearance-field"); densityLabel.append(el("span", "Density"));
  const density = el("select"); density.id = "appearance-density"; density.dataset.appearanceOverride = "density"; density.setAttribute("aria-label", "Density");
  for (const value of ["comfortable", "compact"]) { const option = el("option", value === "comfortable" ? "Comfortable" : "Compact"); option.value = value; density.append(option); }
  densityLabel.append(density); optionRow.append(densityLabel);
  density.addEventListener("change", () => dirtyOverrides.add("density"));
  fieldset.append(optionRow);

  const actions = el("div", undefined, "appearance-actions");
  const apply = el("button", "Apply changes", "appearance-apply"); apply.id = "appearance-apply-overrides"; apply.type = "button";
  const reset = el("button", "Reset appearance"); reset.id = "appearance-reset"; reset.type = "button";
  actions.append(apply, reset); fieldset.append(actions); wrapper.append(fieldset);

  const active = el("p", "", "appearance-active"); active.id = "appearance-active";
  const status = el("p", "", "appearance-status"); status.id = "appearance-status"; status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
  wrapper.append(active, status); host.replaceChildren(wrapper);

  function setStatus(text) { status.textContent = text; }
  function applyTheme(syncOverrideControls = true) {
    const theme = resolveTheme(current, darkQuery.matches);
    for (const [name, value] of Object.entries(cssVariables(theme))) root.style.setProperty(name, value);
    root.style.colorScheme = theme.scheme;
    root.dataset.theme = theme.id;
    root.dataset.themeDensity = theme.tokens.density;
    if (themeColorMeta) themeColorMeta.content = theme.tokens.surfaceCanvas;
    if (schemeMeta) schemeMeta.content = theme.scheme;
    systemOption.textContent = `System (${darkQuery.matches ? "Dark" : "Light"})`;
    presetSelect.value = current.mode === "system" ? "system" : current.preset;
    active.textContent = `Active theme: ${theme.label}`;
    if (syncOverrideControls) {
      colorInputs.accent.value = theme.tokens.accent;
      colorInputs.focus.value = theme.tokens.focus;
      colorInputs.brandAction.value = theme.tokens.brandAction;
      radius.value = String(theme.tokens.radius);
      density.value = theme.tokens.density;
    }
  }
  function persist() {
    const storage = safeStorage();
    if (!storage) { setStatus(STORAGE_BLOCKED); return; }
    try { storage.setItem(STORAGE_KEY, JSON.stringify(current)); setStatus(""); }
    catch { setStatus("Could not save appearance. This choice applies only to this page."); }
  }
  function reject(error) {
    setStatus(`That appearance cannot be applied: ${error.message}. The current theme remains active.`);
    dirtyOverrides.clear(); applyTheme();
  }

  presetSelect.addEventListener("change", () => {
    const next = presetSelect.value === "system"
      ? { ...current, mode: "system" }
      : { ...current, mode: "preset", preset: presetSelect.value };
    try { current = parsePreference(next); }
    catch (error) { reject(error); return; }
    dirtyOverrides.clear(); applyTheme(); persist();
  });
  apply.addEventListener("click", () => {
    const overrides = { ...current.overrides };
    if (dirtyOverrides.size === 0) { setStatus("No appearance changes to apply."); return; }
    for (const key of dirtyOverrides) overrides[key] = key === "radius" ? Number(radius.value) : key === "density" ? density.value : colorInputs[key].value;
    try { current = parsePreference({ ...current, overrides }); }
    catch (error) { reject(error); return; }
    dirtyOverrides.clear(); applyTheme(); persist();
  });
  reset.addEventListener("click", () => {
    current = defaultPreference; dirtyOverrides.clear();
    applyTheme();
    const storage = safeStorage();
    if (!storage) { setStatus(STORAGE_BLOCKED); return; }
    try { storage.removeItem(STORAGE_KEY); setStatus(""); }
    catch { setStatus("Reset for this page, but browser storage could not be updated."); }
  });

  const onSystemChange = () => {
    if (current.mode !== "system") return;
    const hasDraft = dirtyOverrides.size > 0;
    applyTheme(!hasDraft);
    if (hasDraft) setStatus("System colors changed. Your unsaved edits remain in the controls. Applying them checks both system palettes.");
  };
  if (darkQuery.addEventListener) darkQuery.addEventListener("change", onSystemChange);
  else darkQuery.addListener?.(onSystemChange);
  window.addEventListener("storage", (event) => {
    const storage = safeStorage();
    if (!storage || event.storageArea !== storage || (event.key !== STORAGE_KEY && event.key !== null)) return;
    if (event.newValue === null) {
      current = defaultPreference; dirtyOverrides.clear(); applyTheme(); setStatus("Another tab reset the appearance."); return;
    }
    try { current = parsePreference(event.newValue); dirtyOverrides.clear(); applyTheme(); setStatus(""); }
    catch (error) { setStatus(`Another tab saved an invalid appearance (${error.message}). The current theme stays active.`); }
  });

  applyTheme();
  setStatus(initialNote);
}

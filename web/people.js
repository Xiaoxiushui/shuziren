import { fetchJson, rememberAccessCode, withAccessCode } from "./api.js";

rememberAccessCode();

const list = document.querySelector("#people-list");
const retryButton = document.querySelector("#retry-people");
const PROFILE_ID_PATTERN = /^[a-z0-9-]{1,48}$/;
let loading = false;

function renderCard(profile) {
  const card = document.createElement("a");
  card.className = "person-card";
  card.href = withAccessCode(`./index.html?profile=${encodeURIComponent(profile.id)}`);

  const avatar = document.createElement("span");
  avatar.className = "person-avatar";
  avatar.textContent = profile.name.slice(0, 2);

  const body = document.createElement("span");
  body.className = "person-body";
  const name = document.createElement("strong");
  name.textContent = profile.name;
  const intro = document.createElement("span");
  intro.className = "person-intro";
  intro.textContent = profile.intro;
  body.append(name, intro);

  const arrow = document.createElement("span");
  arrow.className = "person-arrow";
  arrow.textContent = "进入 →";

  card.append(avatar, body, arrow);
  return card;
}

async function loadPeople() {
  if (loading) return;
  loading = true;
  retryButton.disabled = true;
  list.setAttribute("aria-busy", "true");
  const loadingMessage = document.createElement("p");
  loadingMessage.className = "people-loading";
  loadingMessage.textContent = "正在加载…";
  list.replaceChildren(loadingMessage);
  try {
    const data = await fetchJson("/api/profiles", { cache: "no-store" });
    const profiles = Array.isArray(data?.profiles) ? data.profiles.filter((profile) =>
      profile && typeof profile.id === "string" && PROFILE_ID_PATTERN.test(profile.id) &&
      typeof profile.name === "string" && profile.name.trim() && typeof profile.intro === "string",
    ) : [];
    if (!profiles.length) throw new Error("profiles_empty");
    list.replaceChildren();
    profiles.forEach((profile) => list.append(renderCard(profile)));
    retryButton.hidden = true;
  } catch (error) {
    console.warn("人物列表加载失败。", error);
    list.replaceChildren();
    const message = document.createElement("p");
    message.className = "people-loading";
    message.textContent = "人物列表加载失败，请重新加载。";
    list.append(message);
    retryButton.hidden = false;
  } finally {
    list.setAttribute("aria-busy", "false");
    retryButton.disabled = false;
    loading = false;
  }
}

retryButton.addEventListener("click", loadPeople);
loadPeople();

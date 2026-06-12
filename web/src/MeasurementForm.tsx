import { useState } from "react";
import type { CSSProperties } from "react";
import type { Measurements } from "./calibration";
import {
  MORPH_LABELS,
  MORPH_ORDER,
  useAvatarStore,
} from "./store";

const FIELDS: { key: keyof Measurements; label: string; unit: string }[] = [
  { key: "height", label: "Рост", unit: "см" },
  { key: "weight", label: "Вес", unit: "кг" },
  { key: "chest", label: "Обхват груди", unit: "см" },
  { key: "waist", label: "Обхват талии", unit: "см" },
  { key: "hips", label: "Обхват бёдер", unit: "см" },
];

export function MeasurementForm() {
  const gender = useAvatarStore((s) => s.gender);
  const setGender = useAvatarStore((s) => s.setGender);
  const measurements = useAvatarStore((s) => s.measurements);
  const setMeasurement = useAvatarStore((s) => s.setMeasurement);
  const resetMeasurements = useAvatarStore((s) => s.resetMeasurements);
  const showClothing = useAvatarStore((s) => s.showClothing);
  const setShowClothing = useAvatarStore((s) => s.setShowClothing);

  const [fineTune, setFineTune] = useState(false);

  return (
    <div style={panel}>
      <div style={title}>Ваши параметры</div>

      {/* Пол */}
      <div style={genderRow}>
        {(["female", "male"] as const).map((g) => (
          <button
            key={g}
            onClick={() => setGender(g)}
            style={{
              ...genderBtn,
              ...(gender === g ? genderBtnActive : {}),
            }}
          >
            {g === "female" ? "Женский" : "Мужской"}
          </button>
        ))}
      </div>

      {/* Мерки */}
      {FIELDS.map((f) => (
        <label key={f.key} style={fieldRow}>
          <span style={fieldLabel}>{f.label}</span>
          <input
            type="number"
            value={measurements[f.key]}
            onChange={(e) =>
              setMeasurement(f.key, Number(e.target.value) || 0)
            }
            style={numInput}
          />
          <span style={unit}>{f.unit}</span>
        </label>
      ))}

      <button style={resetBtn} onClick={resetMeasurements}>
        Сбросить к среднему
      </button>

      <label style={checkRow}>
        <input
          type="checkbox"
          checked={showClothing}
          onChange={(e) => setShowClothing(e.target.checked)}
        />
        <span>Показывать одежду</span>
      </label>

      {/* Тонкая настройка (морфы напрямую) */}
      <button style={toggleBtn} onClick={() => setFineTune((v) => !v)}>
        {fineTune ? "▾ Тонкая настройка" : "▸ Тонкая настройка"}
      </button>
      {fineTune && <FineTune />}
    </div>
  );
}

function FineTune() {
  const morphs = useAvatarStore((s) => s.morphs);
  const setMorph = useAvatarStore((s) => s.setMorph);
  return (
    <div style={{ marginTop: 4 }}>
      {MORPH_ORDER.map((name) => (
        <label key={name} style={sliderRow}>
          <span style={sliderLabel}>{MORPH_LABELS[name]}</span>
          <input
            type="range"
            min={-1.2}
            max={1.6}
            step={0.01}
            value={morphs[name]}
            onChange={(e) => setMorph(name, Number(e.target.value))}
            style={{ flex: 1 }}
          />
        </label>
      ))}
    </div>
  );
}

const panel: CSSProperties = {
  position: "absolute",
  top: 16,
  left: 16,
  width: 280,
  maxHeight: "calc(100vh - 32px)",
  overflowY: "auto",
  padding: "16px 18px",
  background: "rgba(20,20,28,0.85)",
  borderRadius: 14,
  color: "#eee",
  font: "13px system-ui, sans-serif",
  backdropFilter: "blur(8px)",
  userSelect: "none",
};
const title: CSSProperties = {
  fontSize: 16,
  fontWeight: 600,
  marginBottom: 12,
};
const genderRow: CSSProperties = { display: "flex", gap: 8, marginBottom: 14 };
const genderBtn: CSSProperties = {
  flex: 1,
  padding: "8px 0",
  background: "rgba(255,255,255,0.07)",
  color: "#ccc",
  border: "1px solid rgba(255,255,255,0.12)",
  borderRadius: 9,
  cursor: "pointer",
  font: "13px system-ui",
};
const genderBtnActive: CSSProperties = {
  background: "#7c5cff",
  color: "#fff",
  borderColor: "#7c5cff",
};
const fieldRow: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  marginBottom: 9,
};
const fieldLabel: CSSProperties = { flex: 1 };
const numInput: CSSProperties = {
  width: 64,
  padding: "5px 8px",
  background: "rgba(0,0,0,0.3)",
  color: "#fff",
  border: "1px solid rgba(255,255,255,0.15)",
  borderRadius: 7,
  font: "13px system-ui",
  textAlign: "right",
};
const unit: CSSProperties = { width: 22, opacity: 0.6 };
const resetBtn: CSSProperties = {
  marginTop: 6,
  width: "100%",
  padding: "7px 0",
  background: "#7c5cff",
  color: "#fff",
  border: "none",
  borderRadius: 9,
  cursor: "pointer",
  font: "13px system-ui",
};
const checkRow: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  marginTop: 12,
};
const toggleBtn: CSSProperties = {
  marginTop: 12,
  width: "100%",
  padding: "6px 0",
  background: "transparent",
  color: "#aaa",
  border: "1px solid rgba(255,255,255,0.12)",
  borderRadius: 8,
  cursor: "pointer",
  font: "12px system-ui",
  textAlign: "left",
  paddingLeft: 10,
};
const sliderRow: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  marginBottom: 6,
};
const sliderLabel: CSSProperties = { width: 84, fontSize: 12, opacity: 0.85 };

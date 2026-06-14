import { useMemo } from "react";
import type { CSSProperties } from "react";
import { computeFit, ZONE_LABELS, type FitClass } from "./fit";
import { useAvatarStore } from "./store";

const CLASS_COLOR: Record<FitClass, string> = {
  tight: "#e2574c",
  good: "#4caf6a",
  loose: "#5b8def",
};
const CLASS_LABEL: Record<FitClass, string> = {
  tight: "тесно",
  good: "по фигуре",
  loose: "свободно",
};

export function FitPanel() {
  const garment = useAvatarStore((s) => s.garment);
  const measurements = useAvatarStore((s) => s.measurements);
  const selectedSize = useAvatarStore((s) => s.selectedSize);
  const setSelectedSize = useAvatarStore((s) => s.setSelectedSize);

  const fit = useMemo(
    () => (garment ? computeFit(garment, measurements) : null),
    [garment, measurements],
  );

  if (!garment || !fit) return null;

  const activeLabel = selectedSize ?? fit.recommended;
  const active = fit.perSize.find((s) => s.label === activeLabel);

  return (
    <div style={panel}>
      <div style={title}>{garment.title}</div>
      <div style={sub}>Подбор размера</div>

      <div style={sizeRow}>
        {fit.perSize.map((s) => {
          const isRec = s.label === fit.recommended;
          const isActive = s.label === activeLabel;
          return (
            <button
              key={s.label}
              onClick={() => setSelectedSize(s.label)}
              style={{
                ...sizeBtn,
                ...(isActive ? sizeBtnActive : {}),
                ...(isRec ? sizeBtnRec : {}),
              }}
              title={isRec ? "Рекомендуемый размер" : undefined}
            >
              {s.label}
              {isRec ? " ★" : ""}
            </button>
          );
        })}
      </div>

      {active && (
        <>
          <div style={zoneList}>
            {active.zones.map((z) => (
              <div key={z.zone} style={zoneRow}>
                <span style={zoneName}>{ZONE_LABELS[z.zone]}</span>
                <span style={{ ...dot, background: CLASS_COLOR[z.cls] }} />
                <span style={{ ...zoneStatus, color: CLASS_COLOR[z.cls] }}>
                  {CLASS_LABEL[z.cls]}
                </span>
                <span style={zoneEase}>
                  {z.ease >= 0 ? `+${Math.round(z.ease)}` : Math.round(z.ease)} см
                </span>
              </div>
            ))}
          </div>
          <div style={explain}>{fit.explanation}</div>
        </>
      )}
    </div>
  );
}

const panel: CSSProperties = {
  position: "absolute",
  top: 16,
  right: 16,
  width: 250,
  padding: "16px 18px",
  background: "rgba(20,20,28,0.85)",
  borderRadius: 14,
  color: "#eee",
  font: "13px system-ui, sans-serif",
  backdropFilter: "blur(8px)",
  userSelect: "none",
};
const title: CSSProperties = { fontSize: 15, fontWeight: 600 };
const sub: CSSProperties = { fontSize: 12, opacity: 0.6, marginBottom: 12 };
const sizeRow: CSSProperties = {
  display: "flex",
  gap: 6,
  marginBottom: 14,
  flexWrap: "wrap",
};
const sizeBtn: CSSProperties = {
  minWidth: 40,
  padding: "7px 10px",
  background: "rgba(255,255,255,0.07)",
  color: "#ccc",
  border: "1px solid rgba(255,255,255,0.12)",
  borderRadius: 8,
  cursor: "pointer",
  font: "13px system-ui",
};
const sizeBtnActive: CSSProperties = {
  background: "#7c5cff",
  color: "#fff",
  borderColor: "#7c5cff",
};
const sizeBtnRec: CSSProperties = { borderColor: "#4caf6a" };
const zoneList: CSSProperties = { marginBottom: 12 };
const zoneRow: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  marginBottom: 7,
};
const zoneName: CSSProperties = { width: 56 };
const dot: CSSProperties = { width: 10, height: 10, borderRadius: 5 };
const zoneStatus: CSSProperties = { flex: 1, fontSize: 12 };
const zoneEase: CSSProperties = {
  opacity: 0.65,
  fontVariantNumeric: "tabular-nums",
  fontSize: 12,
};
const explain: CSSProperties = {
  fontSize: 12,
  lineHeight: 1.4,
  opacity: 0.85,
  borderTop: "1px solid rgba(255,255,255,0.1)",
  paddingTop: 10,
};

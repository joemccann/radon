import { ImageResponse } from "next/og";

export const size = {
  width: 180,
  height: 180,
};

export const contentType = "image/png";

export default function AppleIcon() {
  return new ImageResponse(
    (
      <div
        style={{
          display: "flex",
          width: "100%",
          height: "100%",
          alignItems: "center",
          justifyContent: "center",
          background: "#0a0f14",
          border: "1px solid #1e293b",
        }}
      >
        <div
          style={{
            display: "flex",
            width: "120px",
            height: "120px",
            alignItems: "center",
            justifyContent: "center",
            border: "1px solid #05AD98",
            color: "#05AD98",
            fontSize: 72,
            fontWeight: 700,
            fontFamily: "Inter, system-ui, sans-serif",
          }}
        >
          R
        </div>
      </div>
    ),
    size,
  );
}

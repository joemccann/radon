// Hand-built conceptual figure (not a screenshot): aggregated dark-pool
// buy-pressure rising into a measurable lead before the lit-market move.
// All colors resolve from the `.fig-svg` token classes in globals.css.
export function FlowLeadFigure() {
  return (
    <div className="fig-svg">
      <svg
        viewBox="0 0 1100 380"
        width="100%"
        role="img"
        aria-label="Two-line plot showing dark-pool buy-pressure rising ahead of price by roughly four trading days"
      >
        <g className="grid">
          <line x1="70" y1="40" x2="1060" y2="40" />
          <line x1="70" y1="110" x2="1060" y2="110" />
          <line x1="70" y1="180" x2="1060" y2="180" />
          <line x1="70" y1="250" x2="1060" y2="250" />
          <line x1="70" y1="320" x2="1060" y2="320" />
        </g>

        <line className="axis" x1="70" y1="40" x2="70" y2="320" />
        <line className="axis" x1="70" y1="320" x2="1060" y2="320" />

        <rect className="lead-band" x="455" y="40" width="120" height="280" />
        <text className="annot" x="515" y="58" textAnchor="middle">
          lead
        </text>
        <text className="annot" x="515" y="72" textAnchor="middle">
          ≈ 4 sessions
        </text>

        <path
          className="line-flow"
          d="M70,250 L110,244 L150,236 L190,238 L230,222 L270,210 L310,214 L350,196 L390,178 L430,158 L470,140 L510,118 L550,104 L590,98 L630,96 L670,100 L710,108 L750,118 L790,128 L830,140 L870,150 L910,158 L950,168 L990,176 L1030,184 L1060,188"
        />

        <path
          className="line-price"
          d="M70,272 L110,270 L150,268 L190,270 L230,266 L270,262 L310,264 L350,260 L390,256 L430,250 L470,244 L510,230 L550,212 L590,192 L630,168 L670,146 L710,128 L750,114 L790,106 L830,102 L870,104 L910,110 L950,118 L990,126 L1030,132 L1060,136"
        />

        <circle className="marker" cx="510" cy="118" r="3.5" />
        <circle className="marker-ring" cx="510" cy="118" r="7" />
        <circle className="marker" cx="630" cy="168" r="3.5" />
        <circle className="marker-ring" cx="630" cy="168" r="7" />

        <text className="annot-serif" x="300" y="150">
          accumulation detected
        </text>
        <text className="annot" x="300" y="168">
          flow score 84 · accelerating
        </text>

        <text className="annot-serif" x="720" y="84" textAnchor="start">
          price confirms
        </text>
        <text className="annot" x="720" y="100" textAnchor="start">
          +11.4% over window
        </text>

        <g>
          <line className="line-flow" x1="80" y1="350" x2="116" y2="350" />
          <text className="annot" x="124" y="354">
            dark-pool buy-pressure (z-score)
          </text>
          <line className="line-price" x1="430" y1="350" x2="466" y2="350" />
          <text className="annot" x="474" y="354">
            realized price (indexed)
          </text>
        </g>

        <text className="axis-label" x="62" y="44" textAnchor="end">
          +2σ
        </text>
        <text className="axis-label" x="62" y="184" textAnchor="end">
          0
        </text>
        <text className="axis-label" x="62" y="324" textAnchor="end">
          −2σ
        </text>
        <text className="axis-label" x="70" y="338">
          t−30
        </text>
        <text className="axis-label" x="510" y="338" textAnchor="middle">
          t−12
        </text>
        <text className="axis-label" x="1060" y="338" textAnchor="end">
          t
        </text>
      </svg>
    </div>
  );
}

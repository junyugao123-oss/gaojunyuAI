# 每日股研AI Commercial Landing Design QA

final result: passed

## Reference

- Visual target: `/var/folders/8_/9rjs3s451_j3f2ncdhlygwqm0000gn/T/codex-clipboard-66edd62c-7ea0-4955-8551-5a3f46560a3c.png`
- Implemented desktop capture: `/tmp/gyai-placeholder-before.png`
- Implemented mobile capture: `/tmp/gyai-landing-mobile.png`

## Checks

- Copy: title matches `输入一只股票，给你最专业的分析`; default search placeholder is `五一视界 HK6651` and becomes an active value on focus.
- Layout: dark hero, ivory search box, curved light report preview, and concise valuation preview match the confirmed direction.
- Page structure: the current landing content is wrapped as the first `.gyai-page`; future MVP video can be added as the next full-page section.
- One-page fit: desktop layout is compressed to fit typical browser viewports around `1280x800` while preserving the richer score and evidence preview.
- Mobile fit: mobile hides the richer score/evidence section and duplicate current-price summary, keeping only conclusion, tags, range, and price marker.
- CTA: analyze button is smaller, more rounded, and flatter than the initial heavy red button.
- Example preview: default `五一视界 HK6651` uses a richer result preview with conclusion, range, current price, health scores, and key evidence.
- Example values: current price is `137.950`港元 and AI valuation range is displayed as `HK$130.00 - HK$181.00` with `港元/股` unit text.
- Range: the bottom `68.0 / 合理区间 / 138` label row has been removed to keep the preview cleaner.
- Range marker: the current-price marker now includes a `当前价格` label above the red price.
- Range colors: valuation rail uses the selected `冷灰琥珀` palette with segment labels for `偏低 / 合理区间 / 偏高`.
- Range mapping: current price inside `130 - 181` now maps to the gold reasonable-range segment instead of the left gray segment.
- Result dimensions: preview includes five health scores and three key evidence rows, styled in the current light visual language without green states.
- Five-factor sample: `五一视界 HK6651` health scores now reflect public 2025 financial data: growth is stronger, while value, profitability, finance, and dividend are more cautious.
- Color: no green token or visible green state in `CommercialLandingPage.tsx` or `CommercialLandingPage.css`; red is limited to CTA/current price marker.
- Interaction: stock search now ignores market-only prefixes such as `hk`, `h股`, `港股`, `a股`, `sh`, and `sz`; actual name/code inputs such as `HK6651`, `6651`, `五一`, `tx`, and `0700` still return matching suggestions.
- Search clearing: after selecting/activating the default stock, clearing the input and focusing it again keeps suggestions hidden until the user types a non-empty query.
- Search activation: single click/tap on the input starts a blank search; desktop double-click fills `五一视界 HK6651`; the example line is static helper text with no click or hover behavior.
- Search dropdown: multiple suggestions render above the curved preview area, use an opaque panel, and scroll inside the menu instead of being clipped by the next section.
- Example preview isolation: selecting another stock in the search box does not change the homepage sample preview; it remains fixed on `五一视界 HK6651`.
- Responsive: 390px mobile capture has no horizontal overflow and keeps login visible.
- Header: top navigation keeps only `A/H股全域数据`, `金融量化算法`, `AI深度推理`, and `登录`.

## Intentional Deviations

- The original dashboard shell is preserved at `/dashboard`; `/` is a public commercial landing page so it can render without the backend auth status endpoint.

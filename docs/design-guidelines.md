# Design Guidelines

> Last updated: 2026-03-03

## UI Principles

- **Data-dense**: Show maximum useful information without clutter
- **Dark mode first**: All components support `dark:` Tailwind variants
- **Vietnamese labels**: UI text uses Vietnamese (e.g., "Vốn", "Phân tích", "Nạp/Rút")
- **Progressive disclosure**: Expandable sections for secondary data (ADV detail, rebalance calculator)

## Color System (Tailwind CSS 4)

### Fill Rate Colors (`frontend/src/lib/format.ts`)
```typescript
fillColor(rate):
  >= 0.9  → text-green-600 dark:text-green-400   // good
  >= 0.8  → text-yellow-600 dark:text-yellow-400  // warning
  < 0.8   → text-red-600 dark:text-red-400        // bad
```

### Trade Colors (Rebalance Calculator)
- Buy orders: `text-green-600 dark:text-green-400`
- Sell orders: `text-red-600 dark:text-red-400`
- Summary cards: green bg for buys, red bg for sells, blue bg for new capital

### Status Colors (Verify Page)
- OK: green
- WARNING: yellow
- ERROR: red

## Component Patterns

### Card Layout
```tsx
// Standard card wrapper
className="bg-white dark:bg-gray-900 rounded-xl shadow-sm dark:shadow-gray-900/20 border border-gray-200 dark:border-gray-700 p-5"
```

### Expandable Sections
Use `<details>/<summary>` for collapsible content (ADV detail, Rebalance Calculator).
```tsx
<details className="... border ...">
  <summary className="px-5 py-3 font-medium cursor-pointer hover:text-blue-600 dark:hover:text-blue-400">
    Section Title
  </summary>
  <div className="px-5 pb-5">...</div>
</details>
```

### Data Tables
- Striped rows: alternating `bg-gray-50/50 dark:bg-gray-800/50`
- Sticky header: `bg-gray-50 dark:bg-gray-800`
- Clickable sort headers: `cursor-pointer hover:text-blue-600 dark:hover:text-blue-400`
- Numeric columns: `text-right font-mono`

### Loading State
```tsx
<div className="animate-spin h-8 w-8 border-4 border-blue-600 border-t-transparent rounded-full" />
```

## Number Formatting (`frontend/src/lib/format.ts`)

| Function | Input | Output | Usage |
|----------|-------|--------|-------|
| `fmtVND(v)` | VND amount | `1.2T`, `3.5B`, `200M` | Portfolio values, trade values |
| `fmtPrice(v)` | VND price | `25.3k` (÷1000) | Stock prices (DB stores in thousands) |
| `fillColor(r)` | 0.0–1.0 | Tailwind class | Fill rate cells |

## Navigation

Root layout (`layout.tsx`) nav links:
- Dashboard (`/`)
- Portfolio (`/portfolio`)
- Config (`/config`)
- Verify (`/verify`)
- Data (`/data`)

## Responsive Layout

- Mobile: single-column stacked cards
- Tablet+: grid layouts (`sm:grid-cols-2`, `sm:grid-cols-3`)
- Desktop: up to 6-column summary grids (`lg:grid-cols-6`)
- Tables: `overflow-x-auto` for horizontal scroll on small screens

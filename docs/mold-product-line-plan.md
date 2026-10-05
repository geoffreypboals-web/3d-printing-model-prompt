# Mold-made products: product ideas and plan

Date: 2026-10-05 · Status: plan, owner-requested (side-business review,
SuiteControl `docs/reviews/2026-10-05-side-business-review.md`, crafting
item 1). Nothing here is a committed design until it ships with its own ADR.

This is the business side of the mold feature. The technique and software
roadmap live in `docs/mold-production-research-and-plan.md`; what's built
is in ADRs 0005-0011. This plan answers a different question: **which
products should the printers' molds make, in what order, and what has to be
built so they can be priced, made and sold?**

The idea in one line: a printed part sells once; a printed **mold** turns
the same design into candles, soap, concrete, plaster and resin pieces,
which are different products for different markets, made from cheap
consumables while the printers do something else.

---

## 1. Which mold mode fits which material

The four modes already exist (`MoldRequest.mode`). What decides the mode is
the casting material's **temperature** and **stiffness**, because a PLA or
PETG mold softens near 60 °C (PLA) to 80 °C (PETG).

| Casting material | Pours at | Mode | Why |
|---|---|---|---|
| Concrete, cement, Jesmonite | room temp | `direct_cast` or `hollow_cast` | Cold pour, so a rigid printed mold is fine. Needs a release agent and no undercuts (run draft-check). |
| Plaster, gypsum (Forton, Hydrocal) | room temp, mild heat as it sets | `direct_cast`, `hollow_cast` | As above; thin parts only in PLA, since thick pours warm up. |
| Epoxy / UV / polyurethane resin | room temp, **heats while curing** | `silicone_block`, `form_fitting` | Thick epoxy pours can pass PLA's softening point. Silicone molds also give a glossy finish and release cleanly. |
| Candle wax (soy, paraffin, beeswax) | roughly 55-90 °C | `silicone_block`, `form_fitting` | Hot. Never pour wax straight into a printed mold. |
| Melt-and-pour soap | roughly 50-65 °C | `silicone_block`, `form_fitting` | Warm and slightly caustic; silicone releases bars cleanly. |
| Cold-process (lye) soap | room temp, caustic, heats while saponifying | `silicone_block` | Lye attacks some plastics; silicone is the standard. |
| Chocolate, candy, ice | varies | `silicone_block` with **food-grade platinum silicone only** | Never let a printed surface touch food (layer lines trap bacteria). See section 4. |

Silicone notes for the `silicone_block` and `form_fitting` paths:
- **Platinum-cure** silicone for anything skin- or food-adjacent, and for
  long mold life. Tin-cure is cheaper but not food-safe and wears faster.
- Platinum cure can fail to set against some surfaces (sulfur-containing
  clays, latex gloves, uncured SLA resin). Printed PLA and PETG are usually
  fine; a test pour on each new filament or coating is cheap insurance.
- Sealing the printed master (primer or clear coat, lightly sanded) removes
  layer lines that silicone would otherwise copy onto every cast.

## 2. Product ideas, ranked

Ranked by how fast they can be on a table and how well they fit the
markets ScoutOps tracks. Effort: S = existing models and modes are enough,
M = needs a new design or one software change, L = new process or
regulatory homework.

| # | Product | Material | Mode | Fits these markets | Effort | Why this rank |
|---|---|---|---|---|---|---|
| 1 | Small succulent planters, matching sets | concrete or Jesmonite | `hollow_cast` | farmers, handmade, family | S | Cold pour, cheap material, rigid mold reused many times, strong craft-fair seller, giftable in sets. |
| 2 | Coasters and trays (geometric, dragon scale, logo) | epoxy resin with pigment or inclusions | `form_fitting` / `silicone_block` | handmade, geek | S | Flat parts, almost no undercuts, high perceived value, custom-order friendly. |
| 3 | Themed candles (dragon eggs, dice, figures from the existing catalog) | soy wax | `silicone_block` | handmade, geek, family | M | Reuses the shop's existing creature designs; repeat buyers. Needs wick channel and labelling (section 4). |
| 4 | Shaped soap bars, same themes | melt-and-pour base | `silicone_block` | farmers, handmade, family | M | Same silicone molds as candles; low ingredient risk with a ready-made base. |
| 5 | Resin keychains, pins, dice | epoxy / UV resin | `form_fitting` | geek | M | Small, cheap, impulse buys; dice need careful bubble and flat-face work. |
| 6 | Plaster or concrete wall plaques and bookends | plaster, Jesmonite | `direct_cast` | handmade | M | Heavy, high price per piece; bookends need inserts (felt pads, weights). |
| 7 | Custom molds as a product (sell the mold or its STL) | printed mold or digital file | any | online (Shopify digital, Etsy) | M | Zero marginal cost for STLs; buyers are other crafters. Only for original designs (section 4). |
| 8 | Chocolate and candy shapes | food-grade silicone | `silicone_block` | farmers, family | L | Strong seasonal seller, but food rules apply (section 4); last on purpose. |

**Start with 1 and 2.** Both are cold pours, need no new code and no
labelling homework, and they test the whole chain (design, mold, cast,
price, sell) for very little money.

## 3. What each product costs (filled in by the trials)

Cost per cast = casting material (cavity volume from the mold result x
density x price per gram) + mold wear (mold cost / casts the mold survives)
+ consumables (wick, fragrance, pigment, release agent, packaging) + labour.

The mold endpoint already returns `cavity_volume_cm3` and, given
`material_density_g_per_cm3`, `estimated_cast_mass_g`. The trials record the
rest per product:

| Product | Material g per cast | Material $ per g | Mold cost | Casts per mold | Consumables $ | Minutes per cast | Sold for |
|---|---|---|---|---|---|---|---|
| Planter | (from mold result) | | | | | | |
| Coaster | | | | | | | |

The finished numbers go into FinanceOps as each product's unit cost
(`Sales & Profit -> Set a product's unit cost`), so the profit report covers
mold-made products like everything else.

## 4. Rules to check before selling

- **Licence of the design.** Only the shop's own designs, or ones whose
  licence allows selling products made from them. A mold of a model counts
  as making products from it. The farm manager's sale check
  (`GET /api/v1/library/files/:id/sale-check`) must say "allowed" before a
  mold of that file is made for sale. Selling molds or mold STLs (idea 7)
  is only for original designs.
- **Candles.** Use warning labels (burn within sight, keep away from
  children and pets, trim wick); the US industry standard for candle fire
  safety labelling is ASTM F2417. Test burn every new design (wick size vs
  diameter) before selling.
- **Soap.** Plain soap sold as soap is simpler than a product that claims
  to moisturise or treat skin, which can make it a regulated cosmetic. Keep
  claims plain and list ingredients.
- **Food (idea 8).** Food-grade platinum silicone only, never a printed
  surface touching food, and the state's cottage-food rules for selling
  homemade food. Decide only after ideas 1-5 are selling.
- **Resin and cement safety** at the workbench: gloves, ventilation, a dust
  mask for dry cement and sanding. Not a selling rule, but it belongs in the
  build steps.

These are reminders to check, not legal advice; the owner confirms each one
for their state before a product goes on sale.

## 5. Plan

Each phase stands alone; the owner can stop after any of them.

**Phase 0 - Pilot two products (no code).**
- Planter set: pick 2-3 existing planter designs, `hollow_cast`, cast 10
  each in concrete or Jesmonite.
- Coasters: one geometric and one creature design, `form_fitting` or
  `silicone_block`, cast 10 each in epoxy.
- Record the section 3 table for each, plus defects (bubbles, release,
  breakage) and mold life. Sell at one market and online; log results in
  ScoutOps and FinanceOps.
- Exit: a cost and price per product, and a keep / drop call.

**Phase 1 - Material-aware mold requests (this repo, M).**
- Add `cast_material` to `MoldRequest` (concrete, plaster, epoxy, wax, soap,
  food_silicone, other) carrying density, maximum pour temperature and
  recommended modes.
- Refuse (422 with a plain reason) a printed-mold mode for a hot or
  heat-curing material, e.g. `direct_cast` with wax; warn on thick epoxy.
- Fill `material_density_g_per_cm3` from the material, so every mold result
  reports cast mass without the caller knowing densities.
- Optional wick channel for candles (a vertical through-hole on the pour
  axis, diameter set by wick size).
- Tests: golden path per material, refusal case, density fill-in. ADR 0014.

**Phase 2 - "Make a mold" in the farm manager (3dPrinterWorkshopManager, M).**
- A library-file action that calls this service's mold endpoint with the
  chosen material and mode, stores the mold STLs as a new revision, and
  shows cavity volume, cast mass and material cost.
- Gate on the sale check: a file not cleared for sale can still get a mold
  for personal use, but the result is marked "not for sale".
- Track mold life: casts made per mold, with a "retire after N" hint.

**Phase 3 - Consumables and costs (FinanceOps / farm manager, M).**
- Consumables list (silicone, wax, wicks, fragrance, resin, hardener,
  cement, pigment, release agent, packaging) with stock levels and a
  low-stock ntfy push (pairs with WP-31 Spoolman for filament).
- Per-product unit cost computed from section 3 and written to FinanceOps
  `product_costs`.

**Phase 4 - Selling (commerce suite, S each).**
- Listing templates per product type in Shopify-App (materials, care,
  candle warnings), drafted for review as usual.
- Pour and demould clips: the timelapse-to-SocialOps path (WP-17) already
  exists; add a short "pour" capture checklist.
- ScoutOps: tag events by which mold products suit them (candles and soap at
  farmers' markets, resin at geek cons) so `scout rank` uses the right fit.

**Phase 5 - Food products (L, owner decision first).** Only after Phase 0-4
products sell, and after the owner checks cottage-food rules.

## 6. Decisions for the owner

1. Which two pilot products (recommended: planters and coasters).
2. Concrete or Jesmonite for planters (Jesmonite is lighter and crisper but
   costs more).
3. Whether to sell molds or mold STLs to other crafters (idea 7).
4. Whether food products (idea 8) are ever in scope.

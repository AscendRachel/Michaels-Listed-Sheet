# Greentek Catalog (v2)

The Listed Sheet as a web app. One office PC is the **server** (database + app); every scraper PC runs the
**scraper** with its own Start/Stop; everyone uses the app in Chrome with their own login.

This is a rebuild of the earlier `GreentekOptimizer` app. It opens the **same database**, speaks the **same scraper
protocol**, and keeps every feature, so you can switch without losing data.

## Move over from the earlier version (data is kept)

1. Close the old server window and the scraper windows.
2. Copy `C:\GreentekOptimizer\data\optimizer.db` into this folder's `data\` folder (create `data` if missing).
   Your products, history, logins and scraper key all carry over.
3. Run `setup.bat`, then `start_server.bat`. Sign in with your existing login.
4. Scraper PCs: copy this folder, run `setup.bat` (answer **Y** for scraper PC), run `start_scraper.bat`.
   The server address and key you already use still work.

## Fresh install

1. Install Python from python.org (tick "Add python.exe to PATH").
2. `setup.bat` (once per PC).
3. Server PC: `start_server.bat`. The first time, create the admin account on that PC.
4. Other PCs need `allow_other_pcs.bat` run once on the server PC, then open `http://<server-ip>:8000`.
5. Admin: **Scraper → Import Listed Sheet** (Google Sheets → File → Download → CSV), then **Team → Add team member**.
6. Scraper PCs: `start_scraper.bat`, enter the server address and the key from **Scraper → Connect a scraper PC**.

Forgot the admin password: `.venv\Scripts\python run.py resetpassword USERNAME`

## What's in the app

| Tab | What it does |
|---|---|
| Today | Every work queue as a card with counts (Amazon status, listing check, Michaels sync, data quality) |
| Catalog | Search, filter, sort, edit; product drawer with pricing breakdown, check history, change log |
| Add products | One at a time with live price + duplicate check, or paste many rows from the sheet |
| Michaels | Upload the Michaels download, preview every change, apply; export price fixes |
| Pricing | One 3PL fee table by weight (edit the tiers when the 3PL changes its rates), buffer and markup, with a live "how many prices change" preview and a price calculator |
| Scraper | Live run progress, scraper PCs, import/export, settings, key, backup |
| Productivity | Per person and day: issues resolved, products touched, which kinds of issue, plus a 7/14/30-day grid. An issue is resolved when an edit leaves a product that had one (problem status, Listing changed, Title mismatch) with none; one product counts once per person per day. Counting starts from the day this was installed |
| Activity | Every change, who and when, searchable |

**Source status from the listing checks** (only for products with inventory on): *OOS Different Item* when the Amazon title shows a different pack count or size (Listing changed); *OOS Variation* when the variation Amazon shows disagrees with the VARIATION column (sizes, counts and colours are compared, not words; nothing is flagged if either side is blank); *Manual Check* also covers a Title mismatch when nothing else is wrong. Accepting a title ("Title is fine"/typing OK) or fixing the variation clears it.
| Team | Logins (admins) |

### The Catalog is a spreadsheet

Same columns, same order as the Listed Sheet (SKU is pinned at the left so it stays visible; hide or show columns with **Columns**).
Work in it the way you do in Google Sheets:

| Do this | How |
|---|---|
| Select cells / rows / a column | Click, drag, Shift+click or arrows. Click a row number for the whole row, a column header for the whole column, the corner for everything |
| Highlight cells that are far apart | Hold **Ctrl** (⌘ on Mac) while you click or drag to add another block, also on row numbers and column headers. Delete, Space (checkboxes) and Copy then apply to every block. Copy needs the blocks to share the same rows or the same columns. **Esc** or a plain click drops the extra blocks |
| See what you copied | After **Ctrl+C** a dashed border (like Google Sheets) marks the copied cells. It stays while you paste (so you can paste the same cells in several places) and goes away with **Esc**, when you type or clear a cell, or when the rows change |
| Select a whole row / column from the keyboard | **Shift+Space** selects the row(s) of the selection, **Ctrl+Space** the column(s), **Ctrl+A** everything |
| Edit a cell | Just type, or press Enter / F2 / double-click. Enter saves and moves down, Tab saves and moves right, Esc cancels. Listed/Inventory toggle with Space |
| Copy / paste | Ctrl+C / Ctrl+V with ranges, to and from Google Sheets or Excel. One value pasted onto a selection fills the whole selection |
| Fill down / clear | Ctrl+D copies the top row of a selection down; Delete clears |
| Undo / redo | Ctrl+Z / Ctrl+Y (last 50 changes) |
| Filter | The row under the headers filters each column: text = "contains", numbers accept `>10`, `5-9`, `(blank)`. Sort with the ⇅ button |
| Keep your place | Rows **stay in the view after you change them**, marked with a note if they no longer match (not faded; if you unchecked Inventory on the row it gets a soft amber tint), until you press **Refresh view** (like a Google Sheets filter view) |
| One-click jobs | **Actions → Use Amazon price as SOURCE PRICE** for the selected rows (or the whole view). Also bulk-set listing status, status counter, category, remarks, inventory/listed |
| Details | Double-click a row number (or Shift+Enter) for the product drawer |

**Every column is editable.**
- *Stored* columns (name, link, ASIN, price, availability, Michaels price/qty, sold counts…) save what you type.
- *Calculated* columns (3PL fee, source + fee, selling price, markup %, automation price, source status, total sold…) are shown in italics. Typing in one **overrides the formula for that product**, like typing over a formula in Sheets; the cell gets an orange corner. **Delete** (or a blank value) puts the formula back. A typed 3PL fee feeds the automation price, and survives pricing-rule changes and re-imports. A typed Source status lasts until the next Amazon check replaces it.
- **SKU** can be edited too; it renames the product everywhere (check history, activity, scraper work).

**Add or delete a column** (admins): **+ Column**, or a column's **▾** menu (also right-click on a header) → *Insert a column to the right*. Pick a name, type (text, number, checkbox, date) and where it goes. Custom columns can be typed in, pasted into, filtered, sorted, copied and hidden like any other. Delete one from its ▾ menu, or with its **Delete** button in the **Columns** dialog (its values are removed for every product after a confirmation). The sheet's built-in columns can be **hidden** (▾ → Hide this column, or **Columns**) but not deleted, because pricing and statuses depend on them.

**Product images (column A):** the 🖼 button at the far left opens a full-screen carousel of the product's marketplace images (arrow keys or ‹ › to move, thumbnails to jump, Esc to close; Enter or Space on the cell also opens it). Products with no images show a dash. The same thumbnails appear in the product drawer. The links come from the **Michaels Download Listing**: on import the app finds the image columns by name (a header containing *image*, *photo*, *picture* or *thumbnail*, either one column per image or several links in one cell) and stores them by SKU, like SKU number, price and quantity. Every new import replaces the images (and, with "clear missing" on, clears them for products not in the file); a download with no image columns leaves existing images alone. If detection misses, open **Columns used** in the import preview and pick the image columns by hand. The column filter has *has images / no images*. The images are loaded from the marketplace by your browser, so the PC needs internet access.

**Title check: "listing changed" vs "rewording".** Amazon often rewrites a title without changing the product, so the check compares *facts* and *key words* rather than raw wording:
- **Facts:** pack counts (`12 Pcs`, `pack of 12`), dimensions (`11.8x15.7 inch` equals `30x40cm`, fractions like `1-1/2"`) and sizes (`8 oz`, `100 yards`). If both titles state one and nothing agrees, the product is flagged **Listing changed** with the reason ("Pack size differs: catalog says 12, Amazon says 24"). A name cut off mid-number is ignored.
- **Words:** the catalog name's words are looked for in the Amazon title, weighted by how identifying they are (a brand or product type counts far more than "set" or "decor"). Plurals, joined words (pillowcase / pillow case), small typos and your own synonym list all count as the same word. An Amazon title that is just a trimmed version of the catalog name counts as fine. Too few key words left gives **Title mismatch** ("Key words not on Amazon: …  New on Amazon: …"). The alert line is the *Title match alert below (%)* setting (default 50).
- **Approval sticks:** type `OK` in the **TITLE CHECK** cell (or use *Actions → Titles are fine*). From then on, if Amazon only rewords that title the approval follows the new wording; if the pack count or size changes, or the title becomes a different product, it is reviewed again. Clearing the cell takes the approval back.
- **Check again on Amazon (scraper):** *Actions → Check again on Amazon* queues the highlighted rows, or every row in the view, at the front of the scraper queue (for example every Out of Stock row, to double-check them). If a run is open they are next in line; if not, a small *Re-check run* is created and starts when someone presses Start on a scraper PC. The scraper PCs still do the checking; the web app never opens Amazon.
- **Slow pages are not reported as sold out:** when the scraper's first read of a page has no price and no stock line (or no delivery date), it waits for the page to finish loading and reads again, and for a no-price page it reloads once before believing it. Real sold-out products read the same every time and are still reported. **Scraper PCs need the new scraper\scraper_app.py** (replace the file; the server needs no change on the PCs).
- **Teach it:** *Scraper → Settings → Words that mean the same* takes one group per line (`sticker, decal`); saving re-checks every title.
- Two queues on **Today**: *Listing changed* (high confidence, the facts disagree) and *Title mismatch* (worth a look). The grid has **AMAZON TITLE** and **TITLE CHECK** columns at the end.
The first start of a new version re-judges every stored title once (about half a second per 5,000 titles).

**Columns not in the app.** *Scrap Price*, *Extracted Delivery TF*, *Delivery Lead Time* and the three *NEW …* (2026 rates) columns from the Google Sheet were removed: the app reads the Amazon price and the Estimated Delivery text itself (the status rules use them directly, including TimeFrame), so the helper columns aren't needed. To put Amazon's price into SOURCE PRICE, copy the **Price** column and paste it onto SOURCE PRICE (`$35.25` is understood), or use *Actions → Use Amazon price as SOURCE PRICE*.

**Pricing has one fee table.** The old "current" rates are the only ones; the 2026 table and the active-table switch are gone. When rates change, edit the fees or weight limits on the **Pricing** tab, or add and remove tiers (weights are rounded to the nearest ounce, then the first tier that fits is used). A database saved by an older version keeps its "current" rates and drops the other table.
**Scraper records.** Every Amazon check stores two things on the product: when it finished (the **Last Optimization Run** column, e.g. `2026-10-02 14:05:33`) and which scraper PC did it (the **SCRAPED BY (PC)** column right next to it). The PC name is the Windows computer name, or whatever is typed in the scraper window's name box. Filter or sort the column to review one PC's work; it's blank for products never scraped. Every individual check, with its time and PC, is also in the product's check history, and the results export has a "Checked by" column.
**Amazon variation.** The scraper also reads which variation Amazon shows as selected on each product page (for example `Color: Beige/Size: 18" x 18" (Pack of 2)`, in the same "Label: value" style as Amazon's own text, joined with `/`) and stores it in **AMAZON VARIATION**, right beside your **VARIATION** column, so you can compare the two. Blank means the page has no variations. If a scraper couldn't read the page, or an older scraper PC doesn't send it, the stored value is kept rather than blanked. Filter, sort, copy and edit it like any column; **Columns → Variation check** shows just the comparison (SKU, name, both variations, status, last check and PC). **Scraper PCs need the new `scraper\scraper_app.py`** (copy it over and restart the scraper); the app itself works with old scrapers. Values appear as products are re-checked.
**Image comparison (Michaels vs Amazon).** Two pinned image columns sit at the far left: **🖼 M** (the Michaels images, filled by the Michaels import) and **🖼 A** (the main Amazon image, read by the scraper on each check and shown as a small thumbnail). Click either one, or double-click a row number and click a thumbnail, to open the **comparison viewer**: the Michaels images on the left (arrow keys, ‹ ›, or the thumbnails to move through them; broken links are skipped and marked) and the Amazon main image on the right, with **Open original** for each. A product with only one of the two shows just that pane. The Amazon image follows the variation Amazon shows as selected. The scraper stores the image address without a size code, and the app asks for the size it needs (small in the grid, large in the viewer). Filter the **🖼 A** column by *has image / no image*. As with the variation, **scraper PCs need the new `scraper\scraper_app.py`**; images appear as products are re-checked, and an older scraper or an unreadable page never blanks one that's already stored. The Michaels images still need a US connection (VPN) on the PC viewing them; Amazon's don't.
Everything is saved to the server as you go and logged in Activity.
The Michaels **SKU NUMBER** is read from the Listed Sheet import and also set by the Michaels upload.

### New compared with the earlier version
- **Faster at scale.** Fee, price, title score and price change are stored on each product, and queues are SQL queries.
  Counting every queue and listing a page takes a few milliseconds on thousands of products (the old app looped over every product in Python on each request).
- **Bulk actions.** Tick rows (or "select all N matching") and set listing status, status counter, category, remarks,
  inventory/listed flags in one go; accept titles; dismiss alerts. Each change is logged per SKU.
- **Saved views.** Save any set of filters, optionally share it with the team.
- **Price arrows.** The Price column compares the Amazon price with the source price: ▲ when Amazon is above it (High Price), ▼ when below it (Low Price). It shows in every queue, including Check Inventory, Out of Stock and the others. There is no separate "Price moved" issue any more; the drawer still has a price sparkline.
- **Pricing preview.** See how many prices rise or fall before you save new rates; saving recalculates everything in one step.
- **Export the current view** to CSV, and export the **Michaels price fixes** list (SKU, current price, automation price).
- **Multi-word search** ("acrylic 12" finds titles containing both), search by SKU/ASIN/UPC/name.
- **One app.** The scraper dashboard is now a tab, with live refresh; light/dark theme; `/` focuses search; layout adapts to narrow screens.
- **Tests.** `pytest` checks the pricing and status rules against known sheet results, plus the whole API and scraper protocol (66 tests).

## Put it online (Docker)

**One app on a server:** `docker-compose.yml` runs the app plus Caddy (automatic HTTPS).
1. `cp .env.example .env`, set `DOMAIN` (and `TZ`).
2. `docker compose up -d --build`
3. `docker compose exec app python run.py adduser NAME --admin`
4. To bring your current catalog: stop the stack, copy `optimizer.db` into the `gt-data` volume (it must be owned by uid 10001, e.g. `docker run --rm -v greentek_gt-data:/data -v "$PWD":/src alpine sh -c "cp /src/optimizer.db /data/ && chown 10001:10001 /data/optimizer.db*"`), start again.
Scraper PCs only change their Server address (and use the scraper key from the Scraper tab).

**Ports.** Everything is in `.env`: `APP_PORT` (the port inside the container, any number), `HTTP_PORT` / `HTTPS_PORT` (what Caddy opens on the server; keep 80/443 for automatic HTTPS), `APP_MEMORY`, `TZ`. Without Docker the app's port is `GT_PORT` (default 8000).

**Several apps on one server:** use `deploy/multi/`. One shared Caddy (`deploy/multi/proxy`) owns ports 80/443 and sends each domain to its app; each app (`deploy/multi/app`, env file from `.env.multi.example`) has its own project name, data volume, login cookie and scraper key and publishes no ports. Add an app: new env file with a different `APP_NAME` and `DOMAIN`, `docker compose --env-file my-app.env up -d --build`, add a site file in `deploy/multi/proxy/sites/`, reload Caddy. Run exactly one copy of an app per data volume (the database is one SQLite file).

**Backups off the server:** `deploy/backup.sh` copies the daily backups out of the container (run it from cron and sync the folder elsewhere).

*(The Docker files were written and checked for syntax, and the app was run with the same settings on two ports side by side, but Docker itself wasn't available where this was built. Try it on a test server first.)*
## Developing

```
python -m venv .venv && .venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
.venv\Scripts\python tests\seed_demo.py 3000    # demo data (set GT_DATA=demo-data first); login demo / demopass123
```

Layout: `app/rules.py` (pure business rules) · `app/catalog.py` (queues, edit, pricing) · `app/imports.py` ·
`app/scraper.py` (runs, work hand-out, backups) · `app/auth.py` · `app/main.py` (routes) · `web/` (no build step).

**New rows and drag-fill in the Catalog sheet:** the bottom of the sheet always has a blank row. Type or paste a product there (SKU, PRODUCT NAME, SOURCE LINK, SOURCE PRICE, WEIGHT and CATEGORY are required), the 3PL fee and automation price work out as you type, then press **Save new rows**. Rows that pass are created and the rest stay marked red (hover the "new" tag for the reason). Unsaved rows are remembered if you reload. Drag the small square at the corner of a selection down to fill: SKU123 continues as SKU124, SKU125…, numbers with two cells continue their step, other values repeat. Dragging past the last row adds new rows. SKUs of existing products are never changed by dragging.

**Sold counts (replaces IMPORTRANGE + COUNTIF):** the Sold counts tab (admins) takes the Google Sheets link and range of each year's sold tracker (2025 `A4:L`, 2026 `A3:H`, as in the sheet's formulas). For every product it counts how many cells in the range equal its SKU (case ignored), stores that in PRODUCT SOLD 2025 / 2026, and TOTAL PRODUCT SOLD adds the two. Press **Save & sync now**, or switch on automatic sync (every 1 to 24 hours while the app is running). The sheets must be shared as "Anyone with the link can view"; otherwise upload the downloaded tab as CSV/XLSX instead. A sync changes nothing if a sheet can't be read, is empty, or contains none of the catalog's SKUs.

**Update to new rows, and deleting rows:** a new row is now added by itself as soon as SKU, PRODUCT NAME, SOURCE LINK, SOURCE PRICE, WEIGHT and CATEGORY are filled in (no Save button or confirmation). Rows still missing something wait at the bottom; a row the server refuses (for example a duplicate SKU) turns red until you edit it. To delete rows, select them and use **Actions → Delete the selected rows…**, or right-click a row number. You are asked to confirm (it lists the SKUs), the rows leave the catalog and the scraper stops checking them, and **Ctrl+Z** brings them back (each deleted product is archived in the `deleted_products` table).

**Getting deleted products back:** Ctrl+Z (or the Undo button in the toast) restores them right after a delete. After a page reload, use **Actions → Restore deleted products…**, which lists recent deletions (who deleted them and when) and restores the ticked ones.

**Clear filters:** the **✕ Clear filters (n)** button in the Catalog toolbar appears whenever something is filtering and turns off every column filter, the search box and the queue in one click.

**Columns dialog:** besides the presets, **Check all** and **Uncheck all** tick or untick every column at once (the image columns and SKU always stay), so you can untick everything and then tick only the columns you want.

**Checkbox columns:** Listed, Inventory and any checkbox column you add show as checkboxes. Click the box to switch that row, or select cells and press Space to switch a whole block. Copy and paste still use TRUE / FALSE, and the filter still offers TRUE / FALSE.

**Long filters:** the sheet sends its filters in the request body, and a value list where most values stay ticked is stored as 'all except …'. This avoids the Error 400 that very long web addresses caused (for example unticking one of 500 long Estimated Delivery texts).

**Show edit history (right-click a cell):** shows who changed that one cell, when, and from what to what, newest first, including TRUE/FALSE cells, typed overrides of calculated columns, SKU renames and custom columns. For the columns the Amazon scraper fills (Price, Availability, Delivery, Source status, Amazon title) it also lists the changes the scraper found, labelled with the scraper PC. Edits made before this feature are read from their saved text; changes made by the Listed Sheet and Michaels imports aren't listed per cell.

**Column protection (like Protect range in Google Sheets):** an admin right-clicks a column header and chooses **Protect this column…** to decide who can edit it: *Nobody (imports only)*, *Only admins*, or *Admins and chosen people*, with an optional note. Protected columns show a lock in the header, and the cells are greyed for people who can't edit them. Typing, pasting, fill, Delete, bulk actions, the product drawer, new rows and the API all obey it (a save that mixes protected and normal cells saves the normal ones). Imports and the scraper are never blocked. SKU NUMBER, EXPORTED MARKETPLACE PRICE and EXPORTED MARKETPLACE INVENTORY start protected as *Nobody (imports only)*; an admin can change or remove that like any other protection, and the change is logged on the Activity tab.

**New rows need nothing:** a row typed into the blank row at the bottom of the Catalog is added as soon as you fill in anything, even a single cell. SKU, name, link, price, weight and category are no longer required. A row with no SKU gets a temporary one (NEW-000001, NEW-000002 …) that you can rename in the SKU cell. Missing pieces appear in the Data quality queues (Missing weight, Missing category, No Amazon link) until filled in, and a row with no Amazon link isn't sent to the scraper. A SKU you do type must be valid and unused. The Add products page still asks for everything.

**Work lists (resolve an issue row by row without losing your place):** open an issue queue or any filtered view and press **📋 Work list**. The rows are frozen and saved on the server, so they stay (a tick marks the ones you edited, resolved ones are no longer faded; a row you edited that now has Inventory unchecked gets a soft amber tint, other edited rows keep their normal look) even if you reload, the PC restarts or the power goes out. After a reload the Catalog reopens your list by itself, and a banner offers any unfinished list from any PC you sign in on. The bar shows rows / edited / resolved / still open. **✔ Finish & confirm** closes the list: if every row is resolved it's confirmed; if some still have the issue you can keep working, show only the open rows, or finish anyway (they're recorded as skipped). **Discard** asks first and can **undo your changes**: every cell you changed in the list's rows goes back to how it was when you started it (using the before-values on the Activity tab). A cell someone else (or the scraper) changed afterwards is left as it is, and protected columns you can't edit are skipped; the dialog says how many. Or choose *Discard, keep my changes* to just close the list. Starting, finishing and discarding lists are logged on the Activity tab. Changing a filter leaves the list (it stays saved); **Leave (keep saved)** does the same on purpose. A list belongs to the person who started it.

**Name, logo and colour per store:** on the Team tab, **Store name & logo** (admins) sets the app name, a 1 to 3 letter mark, an accent colour and an optional logo image (PNG, JPG, WEBP or GIF under 400 KB). They show in the browser tab, the header, the sign-in page and the tab icon. They are stored in that store's own database, so several stores running side by side (one app copy and one database each, behind Caddy) each keep their own look. A new copy starts from BRAND_NAME, BRAND_MARK and BRAND_COLOR in its .env (passed to the app as GT_APP_NAME, GT_APP_MARK, GT_BRAND_COLOR); anything an admin saves in the app wins over those starting values.

**Scraper key prefix:** a new scraper key starts with the store's short mark in lower case (mark CTR gives ctr_..., the default GT gives gt_...), so you can tell which store a key belongs to when several stores run on one server. A store created from .env gets its prefix from BRAND_MARK at its first start. Renaming a store never changes a key that is already in use (that would lock the scraper PCs out); press **New key…** on the Scraper tab to get one with the new prefix, then give it to that store's scraper PCs.

**Release workflow (local PC -> GitHub -> Docker Hub -> server):**
1. Change and test on the local PC.
2. `git push` to GitHub (the repository `Michaels-Listed-Sheet`, private).
3. Publish a version: `git tag v1.0.2` then `git push origin v1.0.2`. GitHub builds the image (`.github/workflows/docker-publish.yml`) and pushes `ascendrachel/michaels-listed-sheet:1.0.2` and `:latest` to Docker Hub. One-time setup: create the Docker Hub repository as private, and add the repository secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` in GitHub (Settings > Secrets and variables > Actions).
4. On the server: set `APP_VERSION=1.0.2` (and `APP_IMAGE=ascendrachel/michaels-listed-sheet`) in `.env`, then `docker compose pull && docker compose up -d`. To go back, put the previous version number and run the same two commands. The database lives in the `gt-data` volume and is not touched by an update.

**Version 1.0.2:** the Listed Sheet import now runs as a background job (progress bar, and the result is kept on the server, so a slow server or a dropped connection no longer leaves the page waiting; one import at a time). A bad `COOKIE_NAME` can no longer crash every sign-in (invalid characters are replaced and a warning is logged), and `deploy/multi/app` no longer needs a nested default, so it also works with the old `docker-compose` v1. Unexpected server errors now reach the screen as a short message with the error type, and the app checks at start-up that it can write to its data folder (it says so clearly, with the `chown` fix, if it can't). Set `COOKIE_NAME` in the app's env file (see `.env.multi.example`).

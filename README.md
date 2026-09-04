# Fakturaskanning (AI invoice extraction MVP)

Ladda upp en leverantörsfaktura (bild eller PDF), låt Claude läsa ut
leverantör, belopp, moms, datum osv., kontrollera/redigera i webbläsaren,
spara, och exportera allt som CSV (kompatibelt med Fortnox/Visma-import).

Byggd med samma stack som de tidigare projekten: statisk `index.html` +
Python serverless API på Vercel + delad Redis-databas (Upstash).

## Engångsinstallation

### 1. Deploya på Vercel
1. Gå till **vercel.com**, logga in med GitHub, **Add New → Project**, välj
   det här repot, klicka **Deploy**.
2. Du får en live-länk, men inget funkar än förrän steg 2 och 3 är klara.

### 2. Databas (Upstash Redis, gratis)
1. I Vercel-projektet: **Storage**-fliken → **Browse Marketplace** →
   **Upstash** → **Redis** → skapa/koppla en databas (kan återanvända en
   befintlig gratis-databas från ett annat projekt - olika projekt kolliderar
   inte så länge de använder olika nyckelnamn, vilket det här gör).
2. **Settings → Environment Variables**, lägg till:
   - `KV_REST_API_URL`
   - `KV_REST_API_TOKEN`

### 3. Klientens inloggningsnyckel
1. **Settings → Environment Variables**, lägg till:
   - `CLIENT_KEY` = ett privat lösenord bara du och kunden känner till
2. Ge kunden länken: `https://DIN-DOMÄN.vercel.app/?key=DERAS_NYCKEL`
   - Öppnar de den en gång sparas nyckeln i webbläsaren.

### 4. AI-nyckel (Anthropic/Claude) - krävs för att extraktionen ska funka
1. Skapa ett konto på **console.anthropic.com**, generera en API-nyckel.
2. **Settings → Environment Variables**, lägg till:
   - `ANTHROPIC_API_KEY` = nyckeln
3. **Deployments** → senaste → **Redeploy** efter alla env-variabler är satta.

**Obs:** varje faktura som skannas kostar några ören i AI-anrop (riktig
kostnad, redan inräknad i affärsmodellens pris). Utan `ANTHROPIC_API_KEY`
visar appen ett tydligt felmeddelande istället för att krascha.

## Testa lokalt innan du visar kunden
```
python3 _local_test_server.py 8130
```
Öppnar `http://localhost:8130` med en låtsas-databas och ett låtsas AI-svar
(ingen riktig Anthropic-nyckel eller Redis behövs för det här) - bra för att
kolla att gränssnittet ser rätt ut innan en riktig deploy.

## Struktur
- `index.html` - hela gränssnittet: nyckel-inloggning, uppladdning, AI-läsning,
  redigerbar granskning, sparade fakturor, CSV-export.
- `api/index.py` - den enda Vercel-entrypointen (samma mönster som
  hallak-demo/invoicer - Vercels Python-byggare vill ha en fil, inte flera).
  Servar även `index.html` direkt eftersom Vercel annars inte publicerar den
  som en separat statisk fil när det bara finns en Python-entrypoint.
- `api/_extract_logic.py` - anropar Claude för att läsa ut fakturafält.
- `api/_invoices_logic.py` - spara/lista/ta bort sparade fakturor.
- `api/_store.py` - Redis-klient (samma som tidigare projekt).
- `api/_auth.py` - kollar `CLIENT_KEY`.
- `_local_test_server.py` - **lokalt testverktyg, deployas inte** - kör hela
  flödet utan riktiga API-nycklar.

## Nästa steg (efter att första kunden sagt ja)
- Flera kunder samtidigt (en `CLIENT_KEY` per byrå istället för en delad)
- Stripe-prenumeration för betalning
- SIE-filexport som alternativ till CSV

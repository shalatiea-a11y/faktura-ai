# Fakturaskanning (AI invoice extraction)

Ladda upp leverantörsfakturor (bilder eller PDF:er, en eller upp till 20 på
en gång), låt Claude läsa ut leverantör, belopp, moms, datum osv. Fakturor
sparas **automatiskt** - inget klick krävs per faktura - men allt som ser
konstigt ut (summan går inte ihop, ett fält saknas, misstänkt dubblett,
orimligt datum) flaggas tydligt så en människa kan kontrollera det. En fil
som innehåller flera fakturor efter varandra (t.ex. ett kontoutdrag) delas
upp automatiskt. Stöd för flera klientföretag per redovisningsbyrå, export
till CSV, och redigering av fakturor även efter att de sparats.

Byggd med samma stack som de tidigare projekten: statisk `index.html` +
Python serverless API på Vercel + delad Redis-databas (Upstash).

## Engångsinstallation

### 1. Deploya på Vercel
1. Gå till **vercel.com**, logga in med GitHub, **Add New → Project**, välj
   det här repot, klicka **Deploy**.

### 2. Databas (Upstash Redis, gratis)
1. I Vercel-projektet: **Storage**-fliken → **Browse Marketplace** →
   **Upstash** → **Redis** → skapa/koppla en databas (kan återanvända en
   befintlig gratis-databas från ett annat projekt).
2. **Settings → Environment Variables**, lägg till:
   - `KV_REST_API_URL`
   - `KV_REST_API_TOKEN`

### 3. Byråns inloggningsnyckel
1. **Settings → Environment Variables**, lägg till:
   - `CLIENT_KEY` = ett privat lösenord bara du och byrån känner till
2. Ge byrån länken: `https://DIN-DOMÄN.vercel.app/?key=DERAS_NYCKEL`

### 4. AI-nyckel (Anthropic/Claude) - krävs för att extraktionen ska funka
1. Skapa ett konto på **console.anthropic.com**, generera en API-nyckel.
2. **Settings → Environment Variables**, lägg till:
   - `ANTHROPIC_API_KEY` = nyckeln
3. **Deployments** → senaste → **Redeploy** efter alla env-variabler är satta.

**Obs:** varje faktura som skannas kostar några ören i AI-anrop (riktig
kostnad, redan inräknad i affärsmodellens pris). Utan `ANTHROPIC_API_KEY`
visar appen ett tydligt felmeddelande istället för att krascha.

### 5. (Valfritt) Automatisk mejl-in
Fakturor som mejlas till en dedikerad inkorg hämtas och bearbetas automatiskt
en gång om dagen (gratis-gränsen på Vercel tillåter cron-jobb max en
gång/dygn - vill ni ha det snabbare krävs Vercel Pro).

1. Skapa ett **nytt, dedikerat Gmail-konto** bara för fakturor (t.ex.
   `dinbyra.fakturor@gmail.com`) - inte ert vanliga konto.
2. På det kontot: **Google Account → Security → 2-Step Verification** (slå på
   om det inte redan är på) → **App passwords** → skapa ett för "Mail",
   kopiera de 16 tecknen.
3. **Settings → Environment Variables** i Vercel, lägg till:
   - `MAIL_ADDRESS` = den nya Gmail-adressen
   - `MAIL_APP_PASSWORD` = de 16 tecknen från steg 2
   - `CRON_SECRET` = en slumpad sträng (minst 16 tecken) - skyddar
     cron-endpointen från att triggas av utomstående
4. Redeploy.
5. Ge den nya mejladressen till byrån. Varje klientföretag i appen får en
   kort **kod** (visas i appen när man valt klienten, t.ex. "KALLES") - be
   den som skickar fakturan skriva koden i ämnesraden så hamnar den rätt.
   Mejl utan igenkänd kod hamnar i en "Okategoriserat"-klient istället för
   att försvinna, så inget går förlorat.

## Testa lokalt innan du visar kunden
```
python3 _local_test_server.py 8130
```
Öppnar `http://localhost:8130` med en låtsas-databas och ett låtsas AI-svar
(ingen riktig Anthropic-nyckel eller Redis behövs).

## Hur det fungerar
1. Byrån väljer/skapar ett **klientföretag** (t.ex. "Kalles Bygg AB").
2. Laddar upp 1-20 fakturor (bild/PDF) på en gång.
3. För varje fil: originalet sparas (bokföringslagen kräver att
   underlaget bevaras, inte bara siffrorna), Claude läser ut en eller flera
   fakturor ur filen, och varje faktura sparas direkt.
4. En faktura flaggas som **"Behöver kontroll"** istället för att tyst
   godkännas om: ett fält saknas, beloppen inte går ihop
   (exkl. moms + moms ≠ totalt), den ser ut som en dubblett av en redan
   sparad faktura, eller ett datum är orimligt (långt bakåt/framåt i tiden).
5. Alla fakturor - flaggade eller ej - går att redigera och rätta även
   efter att de sparats, vilket tar bort flaggan om felet är fixat.
6. **Exportera CSV** laddar ner alla fakturor för valt klientföretag.

## Struktur
- `index.html` - hela gränssnittet: nyckel-inloggning, klientföretag,
  massuppladdning med progress, fakturatabell med statusflaggor, redigering,
  CSV-export.
- `api/index.py` - den enda Vercel-entrypointen (Vercels Python-byggare vill
  ha en fil, inte flera). Servar även `index.html` direkt.
- `api/_extract_logic.py` - anropar Claude, ber alltid om en JSON-array
  (även för enstaka fakturor) eftersom en fil kan innehålla flera.
- `api/_invoices_logic.py` - validering/flaggning, spara/lista/redigera/
  ta bort, dubblettkontroll.
- `api/_companies_logic.py` - klientföretag per byrå.
- `api/_files_logic.py` - sparar originalfilen (bild/PDF) i databasen.
- `api/_mail_logic.py` - Vercel Cron (en gång/dygn) kollar en Gmail-inkorg
  via IMAP (stdlib `imaplib`, ingen OAuth) och kör varje bilaga genom samma
  pipeline som manuell uppladdning.
- `api/_store.py` - Redis-klient (hash-baserad för uppslagning/uppdatering
  per id, samma mönster som Invoicer).
- `api/_auth.py` - kollar `CLIENT_KEY`.
- `_local_test_server.py` - **lokalt testverktyg, deployas inte.**

## Bokföringssystem (Fortnox m.fl.)
Ingen direktkoppling mot Fortnox eller annat bokföringssystem än - byrån
exporterar CSV och bifogar den manuellt i Fortnox. En riktig Fortnox-koppling
(automatiskt förbereda betalning, byrån bara godkänner) kräver att man
registrerar sig som utvecklare hos Fortnox och blir godkänd som
integrationspartner - det är en process på Fortnox sida, inte något som går
att bygga runt. Om/när ni vill gå den vägen: registrera er på
fortnox.se/developer/developer-portal så är den processen igång medan vi
bygger annat; kopplingen görs då som ett utbytbart modul-lager så andra
bokföringssystem kan stödjas på samma sätt.

## Nästa steg (efter att första kunden sagt ja)
- En egen `CLIENT_KEY` per byrå istället för en delad (flera byråer samtidigt)
- Stripe-prenumeration för betalning
- SIE-filexport som alternativ till CSV
- Flera valutor (just nu bara SEK)
- Fortnox-koppling (se ovan) eller snabbare mejl-in (Vercel Pro, eller en
  mejltjänst med webhook för nästan direkt bearbetning istället för en
  gång/dygn)

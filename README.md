# Fakturaskanning (AI invoice extraction)

Ladda upp leverantörsfakturor (bilder eller PDF:er, en eller upp till 20 på
en gång), låt Claude läsa ut leverantör, belopp, moms, datum osv. Fakturor
sparas **automatiskt** - inget klick krävs per faktura - men allt som ser
konstigt ut (summan går inte ihop, ett fält saknas, misstänkt dubblett,
orimligt datum) flaggas tydligt så en människa kan kontrollera det. En fil
som innehåller flera fakturor efter varandra (t.ex. ett kontoutdrag) delas
upp automatiskt. Varje redovisningsbyrå har sitt eget konto (e-post +
lösenord), med flera klientföretag, export till CSV, och redigering av
fakturor även efter att de sparats.

Byggd med samma stack som de tidigare projekten: statiska HTML-sidor +
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

### 3. Kontosystemet
1. **Settings → Environment Variables**, lägg till:
   - `SESSION_SECRET` = en lång, slumpad sträng (minst 32 tecken) - används
     för att signera inloggningssessioner. Byt aldrig ut den i efterhand
     utan att alla måste logga in på nytt.
2. Ge byrån länken: `https://DIN-DOMÄN.vercel.app/` - de skapar sitt eget
   konto själva via **"Skapa konto"** (namn på byrå, e-post, lösenord).
   Varje byrå ser bara sina egna klienter och fakturor.

### 3b. Din egen admin-vy (valfritt, bara för dig som äger tjänsten)
1. **Settings → Environment Variables**, lägg till:
   - `ADMIN_KEY` = en egen hemlig nyckel, bara du känner till den (skild
     från byråernas lösenord).
2. Gå till `https://DIN-DOMÄN.vercel.app/admin.html` (länken finns ingen-
   stans i appen - bara du vet om den) och logga in med `ADMIN_KEY`.
3. Där ser du alla byråer som skapat konto: antal klienter, fakturor, hur
   många som behöver kontroll, totalt belopp, och om de kopplat in mejl-in
   - men aldrig deras sparade fakturor eller filer i detalj.

### 4. AI-nyckel (Anthropic/Claude) - krävs för att extraktionen ska funka
1. Skapa ett konto på **console.anthropic.com**, generera en API-nyckel.
2. **Settings → Environment Variables**, lägg till:
   - `ANTHROPIC_API_KEY` = nyckeln
3. **Deployments** → senaste → **Redeploy** efter alla env-variabler är satta.

**Obs:** varje faktura som skannas kostar några ören i AI-anrop (riktig
kostnad, redan inräknad i affärsmodellens pris). Utan `ANTHROPIC_API_KEY`
visar appen ett tydligt felmeddelande istället för att krascha.

### 5. Automatisk mejl-in (Gmail, kräver engångsuppsättning av dig)
Byrån ansluter sitt Gmail-konto med **en knapp** ("Anslut Gmail" under
Inställningar i appen) - de klistrar aldrig in något lösenord, bara
godkänner åtkomsten hos Google. Men för att den knappen ska fungera måste
DU (en gång, inte per kund) skapa en Google OAuth-klient:

1. Gå till **console.cloud.google.com**, skapa ett nytt projekt (gratis).
2. **APIs & Services → Library**, sök upp **Gmail API**, klicka **Enable**.
3. **APIs & Services → OAuth consent screen**:
   - User type: **External**.
   - Fyll i appnamn (t.ex. "Fakturaskanning"), din e-post som support-mejl.
   - **Scopes**: lägg till `.../auth/gmail.readonly`.
   - **Publishing status**: låt den vara **Testing** till att börja med -
     det är gratis och kräver ingen granskning från Google, men max 100
     "test users" och bara e-postadresser du själv lagt till kan ansluta.
     Under **Test users**, lägg till varje kunds Gmail-adress när de blir
     kund (annars stoppar Google inloggningen med ett felmeddelande).
     Vill ni senare öppna det för alla utan att lägga till adresser manuellt
     krävs en formell Google-verifiering (kräver bl.a. en publik
     integritetspolicy) - inget ni behöver bry er om förrän ni har riktig
     skala.
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Web application**.
   - **Authorized redirect URIs**, lägg till:
     `https://DIN-DOMÄN.vercel.app/api/auth/google/callback`
     (byt ut mot er riktiga Vercel-domän - måste matcha exakt).
   - Klicka **Create** - ni får ett **Client ID** och **Client secret**.
5. I Vercel, **Settings → Environment Variables**, lägg till:
   - `GOOGLE_CLIENT_ID`
   - `GOOGLE_CLIENT_SECRET`
6. Redeploy.

Efter det: varje byrå klickar bara "Anslut Gmail" i Inställningar, godkänner
hos Google, klart. Varje klientföretag i appen får en kort **kod** (visas
under "Fakturor" när man valt klienten, t.ex. "KALLES") - de ber den som
skickar fakturan skriva koden i ämnesraden så hamnar den rätt. Mejl utan
igenkänd kod hamnar i en "Okategoriserat"-klient istället för att försvinna.

Fakturor hämtas en gång om dagen (gratis-gränsen på Vercel tillåter
cron-jobb max en gång/dygn - vill man ha det snabbare krävs Vercel Pro).
Åtkomsten är **läs-only** - appen kan aldrig ändra, radera eller skicka
något i kundens inkorg.

En sak till för dig som driftar tjänsten: `CRON_SECRET` (**Settings →
Environment Variables**) = en slumpad sträng (minst 16 tecken) - skyddar
cron-endpointen `/api/cron/check-mail` från att triggas av utomstående.
Vercel skickar den automatiskt som `Authorization: Bearer <CRON_SECRET>`.

## Testa lokalt innan du visar kunden
```
python3 _local_test_server.py 8130
```
Öppnar `http://localhost:8130` med en låtsas-databas och ett låtsas AI-svar
(ingen riktig Anthropic-nyckel eller Redis behövs). Signup/login går mot
den riktiga kodvägen, bara datan är i minnet.

## Hur det fungerar
1. Byrån skapar ett konto (eller loggar in) på startsidan.
2. Under **Fakturor**: väljer/skapar ett **klientföretag** (t.ex. "Kalles
   Bygg AB"), laddar upp 1-20 fakturor (bild/PDF) på en gång.
3. För varje fil: originalet sparas (bokföringslagen kräver att
   underlaget bevaras, inte bara siffrorna), Claude läser ut en eller flera
   fakturor ur filen, och varje faktura sparas direkt.
4. En faktura flaggas som **"Behöver kontroll"** istället för att tyst
   godkännas om: ett fält saknas, beloppen inte går ihop
   (exkl. moms + moms ≠ totalt), den ser ut som en dubblett av en redan
   sparad faktura, eller ett datum är orimligt (långt bakåt/framåt i tiden).
5. Alla fakturor - flaggade eller ej - går att redigera och rätta även
   efter att de sparats, vilket tar bort flaggan om felet är fixat.
6. **Översikt** visar nyckeltal över alla klienter: antal fakturor, hur
   många som behöver kontroll, totalt belopp, senaste fakturorna.
7. **Exportera CSV** (under Fakturor) laddar ner alla fakturor för valt
   klientföretag.

## Struktur
- `index.html` - publik startsida (marknadsföring, "Skapa konto"/"Logga in").
- `login.html` / `signup.html` - inloggning respektive kontoskapande.
- `app.html` - själva appen (kräver inloggning): Översikt, Fakturor,
  Inställningar, i ett sidofält som blir en hamburgermeny på mobil.
- `admin.html` - ägarens egen översikt över alla byråer (`ADMIN_KEY`, inte
  länkad från appen).
- `api/index.py` - den enda Vercel-entrypointen (Vercels Python-byggare vill
  ha en fil, inte flera). Servar även alla `.html`-sidor direkt.
- `api/_auth.py` - konton: signup/login, lösenordshashning (pbkdf2), och
  signerade inloggningssessioner (`SESSION_SECRET`) - ersätter den gamla
  delade `CLIENT_KEY`.
- `api/_users_logic.py` - kontoinställningar (Gmail-anslutning per byrå).
- `api/_google_oauth.py` - Google OAuth (endast läs-scope) + Gmail API-anrop,
  stdlib `urllib` (ingen google-api-python-client).
- `api/_admin_logic.py` - aggregerad statistik över alla byråer, skild
  auktorisering (`ADMIN_KEY`) från byråernas egna konton.
- `api/_extract_logic.py` - anropar Claude, ber alltid om en JSON-array
  (även för enstaka fakturor) eftersom en fil kan innehålla flera.
- `api/_invoices_logic.py` - validering/flaggning, spara/lista/redigera/
  ta bort, dubblettkontroll - allt skopat per byrå.
- `api/_companies_logic.py` - klientföretag per byrå.
- `api/_files_logic.py` - sparar originalfilen (bild/PDF) i databasen.
- `api/_media.py` - gissar rätt filtyp från filändelsen när webbläsaren
  skickar en otydlig `media_type` (t.ex. `application/octet-stream` för en
  PDF), så en riktig faktura inte skickas till AI:n som fel typ.
- `api/_mail_logic.py` - Vercel Cron (en gång/dygn) loopar över varje byrå
  som anslutit Gmail och kollar inkorgen via Gmail API (läs-only), kör varje
  bilaga genom samma pipeline som manuell uppladdning.
- `api/_store.py` - Redis-klient (hash-baserad för uppslagning/uppdatering
  per id; skickar kommandon som POST-body, inte i URL:en, så det funkar även
  för stora filer).
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
- Stripe-prenumeration för betalning
- Glömt lösenord-flöde (kräver att man skickar riktiga mejl, t.ex. via
  samma SMTP-mönster som Bahaa Sax-projektet)
- SIE-filexport som alternativ till CSV
- Flera valutor (just nu bara SEK)
- Fortnox-koppling (se ovan) eller snabbare mejl-in (Vercel Pro, eller en
  mejltjänst med webhook för nästan direkt bearbetning istället för en
  gång/dygn)

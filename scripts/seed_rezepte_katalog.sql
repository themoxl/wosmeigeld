-- Gerichte-Katalog mit echten Rezepten fuellen (dishes + dish_ingredients).
--
-- Aufruf gegen die NAS-Instanz:
--   docker exec -i finanzplaner-db psql -U postgres -d postgres \
--       -v haushalt=<household-uuid> -f - < seed_rezepte_katalog.sql
--
-- Idempotent: ein Gericht wird ueber (household_id, lower(name)) erkannt und
-- aktualisiert statt doppelt angelegt; seine Zutatenliste wird jedes Mal
-- vollstaendig ersetzt. Ein zweiter Lauf aendert also nichts, und eine hier
-- korrigierte Menge wirkt beim naechsten Lauf sofort.
--
-- Warum die Zutaten geloescht und neu geschrieben werden statt zeilenweise
-- abgeglichen: dish_ingredients hat bewusst keinen eindeutigen Schluessel auf
-- dem Namen (dasselbe Gericht darf "Zwiebeln" zweimal fuehren, etwa fuer
-- Fuellung und Topping). Ein Abgleich ueber den Namen waere also geraten -
-- das Rezept als Ganzes zu ersetzen ist die ehrlichere Operation.
--
-- Die Zutatennamen sind, wo es dasselbe Lebensmittel ist, auf die
-- Schreibweise in ingredient_prices ausgerichtet ("Tomaten stueckig" statt
-- "gehackte Tomaten (Dose)"). Die Preiszuordnung laeuft ueber
-- lower(trim(name)); jede eigene Schreibweise waere sonst eine Zutat ohne
-- Preis, obwohl der Preis im Haushalt steht. Wo das Rezept aber etwas
-- anderes verlangt (Kartoffelstaerke, nicht Maisstaerke), steht das Rezept
-- ueber der Preisliste.
--
-- HERKUNFT: Jede Notiz nennt die Quelle, bei Webquellen mitsamt Link. Die
-- Gerichte 18-27 kommen aus zwei Kochbuch-PDFs und tragen Buch und
-- Rezepttitel in der Notiz. Geschaetzte Mengen stehen hier keine mehr.
--
-- Das Misir Wot war frueher die einzige Ausnahme: Mengen geschaetzt,
-- zugeschrieben an Nisha Voras "Big Vegan Flavor". Nachgeschlagen ergab sich
-- zweierlei - das Rezept steht nirgends online, und das vorliegende PDF
-- gleichen Titels ist das Buch von Alice Beckhams, nicht das von Nisha Vora.
-- Nummer 5 ist deshalb durch eine belegte Fassung mit Quelle ersetzt.
--
-- OHNE PREIS in ingredient_prices sind die neu hinzukommenden Zutaten
-- Golden Syrup, Mandelmilch, Mandelmus, Chiasamen und Schwarzkuemmel. Sie
-- stehen bewusst mit ihrem richtigen Namen da: die Kostenschaetzung dieser
-- Gerichte ist dadurch sichtbar eine Untergrenze, bis ein echter Preis
-- nachgetragen wird. Ein Umbiegen auf "Ahornsirup" waere ein anderes Rezept.
--
-- Ein falscher Haushalt braucht keine eigene Pruefung: dishes.household_id
-- ist ein Fremdschluessel, eine unbekannte UUID bricht die Transaktion ab.

\set ON_ERROR_STOP on

\if :{?haushalt}
\else
\echo 'FEHLT: -v haushalt=<uuid>. Beispiel:'
\echo '  psql -U postgres -d postgres -v haushalt=00000000-0000-0000-0000-000000000001 -f seed_rezepte_katalog.sql'
\quit
\endif

set client_encoding = 'UTF8';

begin;

-- Nachtrag: fuenf Gerichte aus einem frueheren Lauf, die aus dem Gedaechtnis
-- nachgebaut und dabei einer Quelle zugeschrieben waren, die sich so nicht
-- belegen laesst. Vier sind unten durch die nachgeschlagene Fassung ersetzt
-- (teils unter neuem Namen), eines durch die Nudelgerichte der Glow Diaries.
-- Sie werden hier entfernt, damit im Katalog keine erfundene Quellenangabe
-- und keine zwei Fassungen desselben Gerichts stehen bleiben.
delete from dish_ingredients
where household_id = :'haushalt'::uuid
  and dish_id in (select id from dishes
                  where household_id = :'haushalt'::uuid
                    and lower(name) in ('butter-tofu masala',
                                        'miso-sesam-nudeln',
                                        'kokos-kichererbsen-curry',
                                        'pilz-stroganoff mit cashewcreme',
                                        'knuspriger tofu mit erdnusssauce'));
delete from dishes
where household_id = :'haushalt'::uuid
  and lower(name) in ('butter-tofu masala',
                      'miso-sesam-nudeln',
                      'kokos-kichererbsen-curry',
                      'pilz-stroganoff mit cashewcreme',
                      'knuspriger tofu mit erdnusssauce');

-- ---------------------------------------------------------------------------
-- 1. Dahl mit roten Linsen (nach @merashkoch)
-- ---------------------------------------------------------------------------
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Dahl mit roten Linsen', 'dinner', 4, 'vegan',
          E'Quelle: @merashkoch (Instagram), wörtlich übernommen.\n1. Linsen kalt waschen. 2. Zwiebeln, Knoblauch und Ingwer in Öl glasig anbraten. 3. Tomaten und Gewürze dazu, gut verrühren. 4. Linsen unterheben. 5. 1 Liter Wasser angießen, aufkochen. 6. 20-25 Min. köcheln, bis die Linsen weich und cremig sind. 7. Mit Koriander garnieren und abschmecken.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Rote Linsen',        250::numeric, 'g',     1),
  ('Zwiebeln',             2::numeric, 'Stück',    2),
  ('Knoblauch',            3::numeric, 'Zehen', 3),
  ('Ingwer',              20::numeric, 'g',     4),
  ('Rapsöl',               1::numeric, 'EL',    5),
  ('Tomaten stückig',    400::numeric, 'g',     6),
  ('Paprikapulver',        1::numeric, 'TL',    7),
  ('Currypulver',          1::numeric, 'TL',    8),
  ('Garam Masala',         1::numeric, 'TL',    9),
  ('Salz',                          null::numeric, null,   10),
  ('Koriander frisch',   0.5::numeric, 'Bund', 11)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 2. Kartoffelpuffer-Crunchwrap (nach @veganer.wandel)
-- ---------------------------------------------------------------------------
-- Die Wrap- und Kartoffelmengen sind gegenueber dem Original verdoppelt: ein
-- Crunchwrap braucht je einen grossen und einen kleinen Wrap, das Hack aus dem
-- Rezept reicht fuer zwei. Sonst waere die Portionsangabe irrefuehrend.
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Kartoffelpuffer-Crunchwrap', 'dinner', 2, 'vegan',
          E'Quelle: @veganer.wandel (Instagram), wörtlich übernommen.\n1. Kartoffeln grob reiben, gut ausdrücken, dünn als Rösti goldbraun braten. 2. Sojagranulat einweichen und ausdrücken. 3. Zwiebel und Knoblauch anbraten, Walnüsse und Granulat dazu, mit Tomatenmark, Sojasauce, Senf, Gewürzen und Ahornsirup würzen, mit Maisstärke binden. 4. Großen Wrap belegen: Hack, Rösti, Streukäse, Joghurt-Frischkäse-Creme, Salat, Tomate; kleinen Wrap als Deckel auflegen. 5. Ränder einschlagen und von beiden Seiten knusprig braten.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Sojagranulat',                 50::numeric, 'g',     1),
  ('Walnüsse',                     50::numeric, 'g',     2),
  ('Zwiebeln',                      1::numeric, 'Stück',    3),
  ('Knoblauch',                     3::numeric, 'Zehen', 4),
  ('Tomatenmark',                   1::numeric, 'EL',    5),
  ('Sojasauce',                     2::numeric, 'EL',    6),
  ('Senf',                          1::numeric, 'TL',    7),
  ('Paprikapulver geräuchert',      1::numeric, 'TL',    8),
  ('Kreuzkümmel',                   1::numeric, 'TL',    9),
  ('Ahornsirup',                    1::numeric, 'EL',   10),
  ('Maisstärke',                    1::numeric, 'TL',   11),
  ('Kartoffeln',                  400::numeric, 'g',    12),
  ('Tortilla Wraps groß',           2::numeric, 'Stück',   13),
  ('Tortilla Wraps klein',          2::numeric, 'Stück',   14),
  ('Streukäse vegan',              50::numeric, 'g',    15),
  ('Joghurt vegan',               100::numeric, 'g',    16),
  ('Frischkäse vegan',             50::numeric, 'g',    17),
  ('Eisbergsalat',                0.5::numeric, 'Stück',   18),
  ('Tomaten',                     100::numeric, 'g',    19),
  ('Salz',                       null::numeric, null,   20),
  ('Pfeffer gemahlen',           null::numeric, null,   21)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 3. Red Pepper Burrito (nach @veganer.wandel)
-- ---------------------------------------------------------------------------
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Red Pepper Burrito', 'dinner', 4, 'vegan',
          E'Quelle: @veganer.wandel (Instagram), wörtlich übernommen.\n1. Avocados mit Zitronensaft, Koriander und Gewürzen zu Guacamole zerdrücken. 2. Reis kochen. 3. Paprika, Zwiebeln und Chunks anbraten; Tomatenmark, Räucherpaprika und Sahne einrühren, Cherrytomaten dazu, 10 Min. köcheln, würzen. 4. Wraps belegen: Streukäse, Guacamole, Reis, Paprika-Chunk-Mischung. 5. Einrollen und von beiden Seiten anbraten.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Zwiebeln',                      2::numeric, 'Stück',    1),
  ('Paprika Chunks vegan',          1::numeric, 'Pkg',   2),
  ('Paprikapulver geräuchert',      2::numeric, 'TL',    3),
  ('Tomatenmark',                   2::numeric, 'EL',    4),
  ('Sahne vegan',                 200::numeric, 'ml',    5),
  ('Paprika',                       2::numeric, 'Stück',    6),
  ('Tomaten',                     250::numeric, 'g',     7),
  ('Chiliflocken',               null::numeric, null,    8),
  ('Avocado',                       3::numeric, 'Stück',    9),
  ('Zitronen',                    0.5::numeric, 'Stück',   10),
  ('Koriander frisch',            0.5::numeric, 'Bund', 11),
  ('Knoblauch',                     2::numeric, 'Zehen',12),
  ('Tortilla Wraps groß',           4::numeric, 'Stück',   13),
  ('Streukäse vegan',             100::numeric, 'g',    14),
  ('Basmatireis',                 200::numeric, 'g',    15),
  ('Salz',                       null::numeric, null,   16),
  ('Pfeffer gemahlen',           null::numeric, null,   17)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 4. Haehnchen-Vindaloo (nach Jamie Oliver)
-- ---------------------------------------------------------------------------
-- Vorlage ist "Kate Hudsons hot & smoky vindaloo" von jamieoliver.com, dort
-- mit 1 kg Schweinebauch. Hier Haehnchenschenkel, weil ausdruecklich so
-- gewuenscht; Schenkel und nicht Brust, weil Brustfleisch in der langen
-- Schmorzeit trocken wird. Weggelassen: das Raeuchern mit gluehender
-- Holzkohle im Topf - das ist der Showteil des Originals und braucht offene
-- Flamme, Kohle und einen Topf, dem man das zutraut.
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Hähnchen-Vindaloo', 'dinner', 6, 'meat',
          E'Quelle: jamieoliver.com/recipes/pork/hot-smoky-vindaloo-with-pork-belly/ – dort mit Schweinebauch, hier mit Hähnchenschenkeln. Das Räuchern mit Holzkohle aus dem Original ist weggelassen.\n1. Ofen auf 170 Grad vorheizen. 2. Koriandersamen, Kreuzkümmel und Pfefferkörner 1 Min. trocken rösten, mahlen und mit Chilis, Ingwer, Knoblauch, Kurkuma, Öl und Apfelessig zu einer Paste stampfen. 3. Hähnchen in 2-cm-Stücken 10-15 Min. goldbraun braten. 4. Curryblätter und drei Viertel der Paste dazu, Zwiebeln unterrühren, 15 Min. mitbraten. 5. Frische Chilis, Tamarindenpaste und Zucker einrühren, 25 Min. in den Ofen. 6. Fett abschöpfen, 600 ml kochendes Wasser angießen, Zimtstange und Nelken dazu, zugedeckt weitere 30-45 Min. garen, bis das Fleisch zerfällt.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Hähnchenschenkel',           1000::numeric, 'g',     1),
  ('Zwiebeln',                      3::numeric, 'Stück',    2),
  ('Knoblauch',                     6::numeric, 'Zehen', 3),
  ('Ingwer',                       50::numeric, 'g',     4),
  ('Kashmiri-Chilis getrocknet',    2::numeric, 'Stück',    5),
  ('Koriandersamen',                2::numeric, 'TL',    6),
  ('Kreuzkümmel',                   1::numeric, 'TL',    7),
  ('Pfefferkörner schwarz',         1::numeric, 'TL',    8),
  ('Kurkuma',                       1::numeric, 'TL',    9),
  ('Apfelessig',                    1::numeric, 'EL',   10),
  ('Curryblätter',                 15::numeric, 'Stück',   11),
  ('Chili',                         2::numeric, 'Stück',   12),
  ('Tamarindenpaste',               1::numeric, 'EL',   13),
  ('Zucker braun',                  1::numeric, 'EL',   14),
  ('Zimtstange',                    1::numeric, 'Stück',   15),
  ('Nelken',                        4::numeric, 'Stück',   16),
  ('Olivenöl',                      2::numeric, 'EL',   17),
  ('Basmatireis',                 400::numeric, 'g',    18),
  ('Salz',                       null::numeric, null,   19)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 5. Misir Wot - aethiopischer Rotlinsen-Eintopf (Holy Cow Vegan)
-- ---------------------------------------------------------------------------
-- Ersetzt die frueher hier stehende Fassung, deren Mengen geschaetzt und Nisha
-- Voras "Big Vegan Flavor" zugeschrieben waren. Nachgeschlagen: das Rezept
-- steht nirgends online. Statt einer Schaetzung steht hier jetzt ein belegtes
-- Misir Wot einer anderen Quelle - lieber ein nachvollziehbares Rezept als ein
-- erfundenes mit dem richtigen Autorennamen.
--
-- Diese Fassung braucht kein Niter Kibbeh: gebraten wird in Olivenoel. Das ist
-- fuer die Einkaufsliste der Unterschied zwischen kaufbar und nicht kaufbar.
-- Die Mekelesha-Gewuerze stehen einzeln da statt als fertige Mischung, aus
-- demselben Grund - jede einzelne steht im Laden, die Mischung nicht.
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Misir Wot – äthiopischer Rotlinsen-Eintopf', 'dinner', 8, 'vegan',
          E'Quelle: holycowvegan.net/misir-wot-ethiopian-red-lentils/ (Vaishali Honawar), 8 Portionen. Ersetzt eine frühere Fassung mit geschätzten Mengen: Nisha Voras Version aus "Big Vegan Flavor" steht nicht online. Diese kommt ohne Niter Kibbeh aus. Die Mekelesha-Gewürzmischung reicht für 3-4 Töpfe. Reis ist als Beilage ergänzt und steht nicht im Original.\n1. Für die Mekelesha Zimtstangen, Kreuzkümmel, Nelken, Pfefferkörner, Muskat und die Samen aus den Kardamomkapseln 3-5 Min. trocken rösten, abkühlen lassen, fein mahlen und im Glas aufbewahren. 2. Linsen mehrfach waschen, bis das Wasser klar bleibt, und abtropfen lassen. 3. Olivenöl im großen Topf erhitzen, Zwiebel mit einer Prise Salz bei kleiner bis mittlerer Hitze ca. 10 Min. weich und glasig braten. 4. Ingwer und Knoblauch 1 Min. mitbraten, dann Berbere und den grob zerstoßenen Schwarzkümmel einrühren. 5. 60 ml Wasser angießen, zudecken und 20 Min. schmoren, dabei mehrfach umrühren, bis die Zwiebeln tief gebräunt und fast pastös sind – bei Bedarf Wasser nachgießen. 6. Linsen, 600 ml Wasser und das Paprikapulver zugeben und aufkochen. 7. Zugedeckt 10 Min. garen, dann offen weitere 10 Min. unter häufigem Rühren, bis die Flüssigkeit weitgehend eingekocht ist; wer es suppiger mag, hört früher auf. 8. Salzen, 1-2 TL Mekelesha einrühren und abschmecken. 9. Mit Koriander bestreuen, dazu Reis oder Injera.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Rote Linsen',                 300::numeric, 'g',     1),
  ('Zwiebeln',                      1::numeric, 'Stück',    2),
  ('Knoblauch',                     4::numeric, 'Zehen', 3),
  ('Ingwer',                       15::numeric, 'g',     4),
  ('Berbere Gewürzmischung',        1::numeric, 'EL',    5),
  ('Schwarzkümmel',                 1::numeric, 'TL',    6),
  ('Paprikapulver',                 1::numeric, 'EL',    7),
  ('Olivenöl',                      2::numeric, 'EL',    8),
  ('Koriander frisch',           0.25::numeric, 'Bund',  9),
  -- Mekelesha: reicht fuer 3-4 Toepfe, faellt also nicht bei jedem Kochen an.
  ('Zimtstange',                    2::numeric, 'Stück',   10),
  ('Kreuzkümmel',                   1::numeric, 'EL',   11),
  ('Nelken',                       20::numeric, 'Stück',   12),
  ('Pfefferkörner schwarz',         1::numeric, 'EL',   13),
  ('Muskatnuss',                    2::numeric, 'Stück',   14),
  ('Kardamom',                     12::numeric, 'Stück',   15),
  ('Basmatireis',                 400::numeric, 'g',    16),
  ('Salz',                       null::numeric, null,   17)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 6. Kichererbsen-Curry mit Kokosmilch (Nisha Vora)
-- ---------------------------------------------------------------------------
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Kichererbsen-Curry mit Kokosmilch', 'dinner', 6, 'vegan',
          E'Quelle: rainbowplantlife.com/chickpea-curry/ (Nisha Vora).\n1. Koriander- und Kreuzkümmelsamen in Öl rösten, Curryblätter dazu, dann Zwiebeln mit Salz 5 Min. anbraten. 2. Knoblauch, Ingwer und Chili 1-2 Min. mitbraten, gemahlene Gewürze und Tomatenmark 90 Sek. rösten. 3. Tomaten dazu, 5 Min. weich schmoren. 4. Kichererbsen, Kokosmilch, 120 ml Wasser, Tahini und Zucker dazu, zugedeckt 15-20 Min. köcheln. 5. Mangold in Streifen unterheben, 4-5 Min. zusammenfallen lassen, Garam Masala einrühren. 6. Vom Herd: Zitronensaft, Koriander und Minze dazu, 5-15 Min. ruhen lassen. Mit Reis servieren.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Kichererbsen',                850::numeric, 'g',     1),
  ('Kokosmilch',                  400::numeric, 'ml',    2),
  ('Zwiebeln',                      1::numeric, 'Stück',    3),
  ('Knoblauch',                     6::numeric, 'Zehen', 4),
  ('Ingwer',                       30::numeric, 'g',     5),
  ('Chili',                         2::numeric, 'Stück',    6),
  ('Tomaten',                     230::numeric, 'g',     7),
  ('Tomatenmark',                   2::numeric, 'EL',    8),
  ('Tahini',                        3::numeric, 'EL',    9),
  ('Koriandersamen',                2::numeric, 'TL',   10),
  ('Kreuzkümmel',                   1::numeric, 'TL',   11),
  ('Currypulver',                 1.5::numeric, 'TL',   12),
  ('Koriander gemahlen',            1::numeric, 'TL',   13),
  ('Zimt',                        0.5::numeric, 'TL',   14),
  ('Muskatnuss',                  0.5::numeric, 'TL',   15),
  ('Kurkuma',                    0.25::numeric, 'TL',   16),
  ('Garam Masala',                  2::numeric, 'TL',   17),
  ('Curryblätter',                 15::numeric, 'Stück',   18),
  ('Mangold',                     200::numeric, 'g',    19),
  ('Zitronen',                    0.5::numeric, 'Stück',   20),
  ('Koriander frisch',            0.5::numeric, 'Bund', 21),
  ('Minze',                       0.5::numeric, 'Bund', 22),
  ('Rapsöl',                        2::numeric, 'EL',   23),
  ('Basmatireis',                 400::numeric, 'g',    24),
  ('Salz',                          null::numeric, null,   25)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 7. Tofu Tikka Masala (Nisha Vora)
-- ---------------------------------------------------------------------------
-- Ersetzt das frueher hier stehende "Butter-Tofu Masala", das nur aus dem
-- Gedaechtnis rekonstruiert war. Der Aufwand ist echt: die Marinade will
-- 2-8 Stunden, das gehoert auf einen Tag mit Vorlauf.
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Tofu Tikka Masala', 'dinner', 6, 'vegan',
          E'Quelle: rainbowplantlife.com/tofu-tikka-masala/ (Nisha Vora). Marinierzeit 2-8 Std. einplanen.\n1. Tofu 20-30 Min. pressen, in Stücke reißen. 2. Ganze Gewürze 2-3 Min. rösten, mahlen, mit den gemahlenen mischen. 3. Gewürzmischung mit Joghurt, Knoblauch, Ingwer, Zitronensaft, Öl und Salz verrühren, Tofu darin 2-8 Std. marinieren. 4. Pfanne stark vorheizen, Tofu 10-12 Min. unter dem Grill rösten, bis er dunkle Stellen hat. 5. Für die Masala Kreuzkümmel und getrocknete Chilis in Öl rösten, Zwiebel goldbraun braten, Knoblauch, Ingwer, Chili und Gewürze dazu, Tomatenmark und Tomaten 15 Min. einkochen; Kokosmilch, Garam Masala, Bockshornkleeblätter und vegane Butter einrühren. 6. Tofu und Zitronensaft dazu, 2 Min. ziehen lassen, mit Koriander und Reis servieren.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Tofu natur',                  800::numeric, 'g',     1),
  ('Joghurt vegan',               230::numeric, 'g',     2),
  ('Knoblauch',                     9::numeric, 'Zehen', 3),
  ('Ingwer',                       50::numeric, 'g',     4),
  ('Zitronen',                      1::numeric, 'Stück',    5),
  ('Rapsöl',                        5::numeric, 'EL',    6),
  ('Kreuzkümmel',                   4::numeric, 'TL',    7),
  ('Koriandersamen',                4::numeric, 'TL',    8),
  ('Nelken',                        6::numeric, 'Stück',    9),
  ('Pfefferkörner schwarz',         1::numeric, 'TL',   10),
  ('Kardamom',                      6::numeric, 'Stück',   11),
  ('Zimtstange',                    2::numeric, 'Stück',   12),
  ('Bockshornkleeblätter',        2.5::numeric, 'EL',   13),
  ('Chilipulver',                 2.5::numeric, 'TL',   14),
  ('Kurkuma',                     1.5::numeric, 'TL',   15),
  ('Paprikapulver',                 1::numeric, 'TL',   16),
  ('Muskatnuss',                  0.5::numeric, 'TL',   17),
  ('Chili getrocknet',              6::numeric, 'Stück',   18),
  ('Zwiebeln',                      1::numeric, 'Stück',   19),
  ('Chili',                         1::numeric, 'Stück',   20),
  ('Tomatenmark',                   2::numeric, 'EL',   21),
  ('Tomaten stückig',             450::numeric, 'g',    22),
  ('Koriander frisch',              1::numeric, 'Bund', 23),
  ('Garam Masala',                  1::numeric, 'EL',   24),
  ('Kokosmilch',                  400::numeric, 'ml',   25),
  ('Butter vegan',                  1::numeric, 'EL',   26),
  ('Basmatireis',                 400::numeric, 'g',    27),
  ('Salz',                          null::numeric, null,   28)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 8. Chana Masala (Nisha Vora)
-- ---------------------------------------------------------------------------
-- Bewusst neben dem Kichererbsen-Curry: dasselbe Grundnahrungsmittel, aber
-- tomatenbasiert und ohne Kokos - im Einkauf und am Tisch zwei verschiedene
-- Gerichte, nicht dasselbe zweimal.
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Chana Masala', 'dinner', 6, 'vegan',
          E'Quelle: rainbowplantlife.com/instant-pot-chana-masala/ (Nisha Vora). Original für den Schnellkochtopf; im normalen Topf stattdessen zugedeckt 30-35 Min. köcheln.\n1. Schwarze Senfsamen und Kreuzkümmel 30 Sek. in Öl rösten, Zwiebeln 6-7 Min. goldbraun braten. 2. Knoblauch, Ingwer, Chili und Lorbeer 1-2 Min. mitbraten. 3. Gemahlene Gewürze 30 Sek. rösten, dann Salz, Tomaten, Kichererbsen und Brühe dazu. 4. Zugedeckt garen (Schnellkochtopf 8 Min., sonst 30-35 Min.). 5. Bei Bedarf offen einkochen lassen. 6. Zitronensaft und Koriander einrühren, mit Reis oder Fladenbrot servieren.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Kichererbsen',               1300::numeric, 'g',     1),
  ('Tomaten stückig',             400::numeric, 'g',     2),
  ('Zwiebeln',                      2::numeric, 'Stück',    3),
  ('Knoblauch',                     4::numeric, 'Zehen', 4),
  ('Ingwer',                       25::numeric, 'g',     5),
  ('Chili',                         2::numeric, 'Stück',    6),
  ('Senfkörner schwarz',            2::numeric, 'TL',    7),
  ('Kreuzkümmel',                 1.5::numeric, 'TL',    8),
  ('Lorbeerblätter',                3::numeric, 'Stück',    9),
  ('Koriander gemahlen',            2::numeric, 'TL',   10),
  ('Kurkuma',                       1::numeric, 'TL',   11),
  ('Chilipulver',                   1::numeric, 'TL',   12),
  ('Garam Masala',                  1::numeric, 'EL',   13),
  ('Pfeffer gemahlen',              null::numeric, null,   14),
  ('Gemüsebrühe',                 360::numeric, 'ml',   15),
  ('Zitronen',                    0.5::numeric, 'Stück',   16),
  ('Koriander frisch',              1::numeric, 'Bund', 17),
  ('Rapsöl',                        3::numeric, 'EL',   18),
  ('Basmatireis',                 400::numeric, 'g',    19),
  ('Salz',                          null::numeric, null,   20)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 9. Karamellisierte-Zwiebel-Pasta (Nisha Vora)
-- ---------------------------------------------------------------------------
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Karamellisierte-Zwiebel-Pasta', 'dinner', 3, 'vegan',
          E'Quelle: rainbowplantlife.com/vegan-caramelized-onion-pasta/ (Nisha Vora). Neun Zutaten, aber 20-25 Min. Geduld bei den Zwiebeln – daran hängt der ganze Geschmack.\n1. Pasta kochen, mindestens 240 ml Kochwasser aufheben. 2. Zwiebeln in Olivenöl mit Salz und Pfeffer anbraten, alle paar Minuten mit einem Schuss Wasser ablöschen, bis sie nach ca. 15 Min. tiefgolden sind. 3. Knoblauch dazu, weitere 5-10 Min. mitkaramellisieren. 4. Chiliflocken und Oregano 30 Sek., dann Tomatenmark 2-3 Min. unter Rühren dunkel rösten. 5. Sojasauce, Hefeflocken und getrocknete Tomaten einrühren. 6. Pasta mit 180-240 ml Kochwasser 3-4 Min. in der Sauce schwenken, bis alles überzogen ist.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Spaghetti',                   285::numeric, 'g',     1),
  ('Zwiebeln',                      2::numeric, 'Stück',    2),
  ('Knoblauch',                     4::numeric, 'Zehen', 3),
  ('Tomatenmark',                 150::numeric, 'g',     4),
  ('Sojasauce',                     2::numeric, 'EL',    5),
  ('Hefeflocken',                   2::numeric, 'EL',    6),
  ('Tomaten getrocknet in Öl',      5::numeric, 'Stück',    7),
  ('Chiliflocken',                  1::numeric, 'TL',    8),
  ('Oregano',                       1::numeric, 'TL',    9),
  ('Olivenöl',                      2::numeric, 'EL',   10),
  ('Salz',                       null::numeric, null,   11),
  ('Pfeffer gemahlen',           null::numeric, null,   12)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 10. Cremiges Pilz-Stroganoff (Nisha Vora)
-- ---------------------------------------------------------------------------
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Cremiges Pilz-Stroganoff', 'dinner', 6, 'vegan',
          E'Quelle: rainbowplantlife.com/creamy-vegan-mushroom-stroganoff/ (Nisha Vora).\n1. Pilze und Lauch portionsweise in Öl 8-10 Min. kräftig braun braten, dabei je die Hälfte Knoblauch, Thymian und Salz zugeben; herausnehmen. 2. Brühe, Sojasauce, Worcestersauce und Mehl glatt verquirlen. 3. Pfanne mit Weißwein ablöschen, 3 Min. einkochen. 4. Brühemischung, Kokosmilch, Tahini, Hefeflocken und Paprikapulver dazu, 10 Min. sämig köcheln. 5. Pasta kochen, Senf in die Sauce rühren, Pasta und Dill unterheben. 6. Mit den beiseitegestellten Pilzen und Dill anrichten.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Champignons frisch',          570::numeric, 'g',     1),
  ('Lauch',                         3::numeric, 'Stück',    2),
  ('Knoblauch',                     6::numeric, 'Zehen', 3),
  ('Thymian getrocknet',            1::numeric, 'TL',    4),
  ('Gemüsebrühe',                 360::numeric, 'ml',    5),
  ('Sojasauce',                     2::numeric, 'EL',    6),
  ('Worcestersauce',                1::numeric, 'EL',    7),
  ('Weizenmehl Type 405',          30::numeric, 'g',     8),
  ('Weißwein',                    120::numeric, 'ml',    9),
  ('Kokosmilch',                  400::numeric, 'ml',   10),
  ('Tahini',                        2::numeric, 'EL',   11),
  ('Hefeflocken',                   2::numeric, 'EL',   12),
  ('Paprikapulver',                 1::numeric, 'TL',   13),
  ('Senf',                        0.5::numeric, 'TL',   14),
  ('Nudeln',                      340::numeric, 'g',    15),
  ('Dill',                       0.25::numeric, 'Bund', 16),
  ('Olivenöl',                      4::numeric, 'EL',   17),
  ('Salz',                       null::numeric, null,   18),
  ('Pfeffer gemahlen',           null::numeric, null,   19)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 11. Erdnuss-Tofu aus dem Ofen (Nisha Vora)
-- ---------------------------------------------------------------------------
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Erdnuss-Tofu aus dem Ofen', 'dinner', 4, 'vegan',
          E'Quelle: rainbowplantlife.com/baked-peanut-tofu/ (Nisha Vora).\n1. Ofen auf 220 Grad vorheizen, Tofu 10-15 Min. pressen, würfeln, mit Öl, Salz, Pfeffer und Kartoffelstärke mischen und auf ein Blech geben. 2. Erdnussmus, Knoblauch, Tamarinde, Limettenabrieb, Sojasauce, Sambal Oelek, Zucker, Koriander und Zimt verrühren. 3. Tofu 20 Min. backen, mit der Hälfte der Sauce und Sesam mischen, weitere 10 Min. bei 175 Grad backen. 4. Brokkoli und Edamame 3 Min. blanchieren, kalt abschrecken, mit Sesamöl und Salz mischen. 5. Restliche Sauce mit Wasser gießfähig rühren. 6. Reis, Gemüse und Tofu anrichten, Sauce darüber, mit Cashews, Koriander und Limette servieren.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Tofu natur',                  400::numeric, 'g',     1),
  ('Kartoffelstärke',               2::numeric, 'EL',    2),
  ('Erdnussmus',                   56::numeric, 'g',     3),
  ('Knoblauch',                     1::numeric, 'Zehen', 4),
  ('Tamarindenpaste',              20::numeric, 'g',     5),
  ('Limette',                       1::numeric, 'Stück',    6),
  ('Sojasauce',                    25::numeric, 'g',     7),
  ('Sambal Oelek',                 15::numeric, 'g',     8),
  ('Kokosblütenzucker',             8::numeric, 'g',     9),
  ('Koriander gemahlen',          0.5::numeric, 'TL',   10),
  ('Zimt',                       null::numeric, null,   11),
  ('Brokkoli',                    400::numeric, 'g',    12),
  ('Edamame',                     170::numeric, 'g',    13),
  ('Sesamöl',                     1.5::numeric, 'TL',   14),
  ('Sesam',                         1::numeric, 'EL',   15),
  ('Jasminreis',                  250::numeric, 'g',    16),
  ('Cashewkerne',                  45::numeric, 'g',    17),
  ('Koriander frisch',            0.5::numeric, 'Bund', 18),
  ('Rapsöl',                      1.5::numeric, 'EL',   19),
  ('Salz',                       null::numeric, null,   20)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 12. Scharfe Knoblauchöl-Nudeln (Glow Diaries)
-- ---------------------------------------------------------------------------
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Scharfe Knoblauchöl-Nudeln', 'lunch', 2, 'vegan',
          E'Quelle: glow-diaries.com/garlic-oil-noodles/ (Liz Douglas). 10 Minuten.\n1. Nudeln nach Packung kochen und abgießen. 2. Frühlingszwiebeln, Knoblauch, Chiliflocken, Chilipulver, Sojasauce und Zucker in einer hitzefesten Schüssel mischen. 3. Öl, Butter und Sesamöl in der Pfanne erhitzen, bis es zischt. 4. Das heiße Öl über die Mischung gießen – es brodelt. 5. Umrühren, sobald es sich beruhigt hat, Nudeln zugeben. 6. Durchschwenken und sofort mit Sesam und Koriander servieren.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Udon-Nudeln',                 200::numeric, 'g',     1),
  ('Frühlingszwiebeln',             4::numeric, 'Stück',    2),
  ('Knoblauch',                     4::numeric, 'Zehen', 3),
  ('Chiliflocken',                 10::numeric, 'g',     4),
  ('Chilipulver',                   2::numeric, 'g',     5),
  ('Sojasauce',                    60::numeric, 'ml',    6),
  ('Zucker',                        4::numeric, 'g',     7),
  ('Butter vegan',                 28::numeric, 'g',     8),
  ('Sesamöl',                       9::numeric, 'g',     9),
  ('Rapsöl',                       50::numeric, 'g',    10),
  ('Sesam',                         1::numeric, 'EL',   11),
  ('Koriander frisch',           0.25::numeric, 'Bund', 12)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 13. Scharfe Erdnuss-Nudeln mit Tofu (Glow Diaries)
-- ---------------------------------------------------------------------------
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Scharfe Erdnuss-Nudeln mit Tofu', 'dinner', 2, 'vegan',
          E'Quelle: glow-diaries.com/peanut-noodles/ (Liz Douglas). 15 Minuten.\n1. Ingwer, Knoblauch, Sojasauce, Erdnussmus, Sriracha und Räucherpaprika glatt rühren. 2. Nudeln kochen, 120 ml Kochwasser aufheben. 3. Tofu in Öl je Seite 2-3 Min. knusprig braten. 4. Kochwasser in die Erdnusssauce rühren. 5. Nudeln und Tofu in der Sauce schwenken. 6. Mit Frühlingszwiebeln, Sesam und Koriander servieren.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Udon-Nudeln',                 400::numeric, 'g',     1),
  ('Tofu natur',                  450::numeric, 'g',     2),
  ('Erdnussmus',                   90::numeric, 'g',     3),
  ('Sojasauce',                    30::numeric, 'g',     4),
  ('Ingwer',                        5::numeric, 'g',     5),
  ('Knoblauch',                     2::numeric, 'Zehen', 6),
  ('Sriracha',                      8::numeric, 'g',     7),
  ('Paprikapulver geräuchert',      2::numeric, 'g',     8),
  ('Rapsöl',                       14::numeric, 'g',     9),
  ('Frühlingszwiebeln',             2::numeric, 'Stück',   10),
  ('Sesam',                         1::numeric, 'EL',   11),
  ('Koriander frisch',           0.25::numeric, 'Bund', 12)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 14. Teigtaschen-Auflauf (Glow Diaries)
-- ---------------------------------------------------------------------------
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Teigtaschen-Auflauf', 'dinner', 4, 'vegan',
          E'Quelle: glow-diaries.com/dumpling-bake/ (Liz Douglas). 25 Minuten, alles in einer Form.\n1. Ofen auf 200 Grad vorheizen. Currypaste, Ingwer, Knoblauch, Miso, Reisessig, Sesamöl, Zucker und Erdnussmus in der Auflaufform glatt rühren. 2. Kokosmilch unterrühren. 3. Pak Choi und Edamame einrühren und verteilen. 4. Gefrorene Teigtaschen daraufsetzen und mit Sauce bestreichen. 5. 15-18 Min. backen, bis die Teigtaschen gar sind und an den Rändern knuspern. 6. Mit Koriander, Frühlingszwiebeln, Chiliöl und Sesam servieren.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Teigtaschen TK vegan',        300::numeric, 'g',     1),
  ('Kokosmilch',                  400::numeric, 'ml',    2),
  ('Massaman-Currypaste',          45::numeric, 'g',     3),
  ('Miso hell',                    30::numeric, 'g',     4),
  ('Reisessig',                    22::numeric, 'g',     5),
  ('Sesamöl',                      13::numeric, 'g',     6),
  ('Erdnussmus',                   16::numeric, 'g',     7),
  ('Zucker braun',                  4::numeric, 'g',     8),
  ('Ingwer',                        5::numeric, 'g',     9),
  ('Knoblauch',                     2::numeric, 'Zehen',10),
  ('Pak Choi',                    250::numeric, 'g',    11),
  ('Edamame',                     120::numeric, 'g',    12),
  ('Frühlingszwiebeln',             2::numeric, 'Stück',   13),
  ('Koriander frisch',           0.25::numeric, 'Bund', 14),
  ('Sesam',                         1::numeric, 'EL',   15)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 15.-17. Drei einfache Gerichte ohne Vorlage
-- ---------------------------------------------------------------------------
-- Keine Quelle, weil es keine gibt: das sind Standardgerichte, wie sie in
-- hundert Varianten herumliegen. Sie stehen hier, weil eine Woche auch Tage
-- braucht, an denen nichts eingeweicht, mariniert oder karamellisiert werden
-- muss.
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Cremige Tomaten-Gnocchi', 'dinner', 4, 'vegan',
          E'Ohne Vorlage, einfaches Ein-Pfannen-Gericht, ca. 20 Minuten.\n1. Zwiebel und Knoblauch in Olivenöl anbraten. 2. Tomatenmark kurz mitrösten. 3. Passierte Tomaten und Sahne dazu, aufkochen. 4. Gnocchi roh einrühren und 8-10 Min. in der Sauce garen. 5. Mit Salz, Pfeffer und Basilikum abschmecken.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Gnocchi',                     800::numeric, 'g',     1),
  ('Passierte Tomaten',           400::numeric, 'g',     2),
  ('Sahne vegan',                 200::numeric, 'ml',    3),
  ('Zwiebeln',                      1::numeric, 'Stück',    4),
  ('Knoblauch',                     2::numeric, 'Zehen', 5),
  ('Tomatenmark',                   1::numeric, 'EL',    6),
  ('Olivenöl',                      2::numeric, 'EL',    7),
  ('Basilikum frisch',              1::numeric, 'Bund',  8),
  ('Salz',                       null::numeric, null,    9),
  ('Pfeffer gemahlen',           null::numeric, null,   10)
) as z(name, menge, einheit, sortierung);

with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Ofengemüse-Bowl mit Hummus-Dressing', 'lunch', 4, 'vegan',
          E'Ohne Vorlage. Ein Blech, eine Schüssel, fertig.\n1. Süßkartoffeln, Brokkoli, Zwiebel und abgetropfte Kichererbsen mit Öl, Paprikapulver, Salz und Pfeffer mischen und 25-30 Min. bei 200 Grad backen. 2. Couscous mit kochendem Wasser übergießen und quellen lassen. 3. Hummus mit Zitronensaft und 3-4 EL Wasser zu einem gießfähigen Dressing rühren. 4. Alles in Schüsseln schichten und mit dem Dressing übergießen.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Süßkartoffeln',               600::numeric, 'g',     1),
  ('Kichererbsen',                400::numeric, 'g',     2),
  ('Brokkoli',                    300::numeric, 'g',     3),
  ('Zwiebeln',                      1::numeric, 'Stück',    4),
  ('Olivenöl',                      3::numeric, 'EL',    5),
  ('Paprikapulver',                 2::numeric, 'TL',    6),
  ('Hummus',                      200::numeric, 'g',     7),
  ('Zitronen',                      1::numeric, 'Stück',    8),
  ('Couscous',                    200::numeric, 'g',     9),
  ('Salz',                       null::numeric, null,   10),
  ('Pfeffer gemahlen',           null::numeric, null,   11)
) as z(name, menge, einheit, sortierung);

with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Zitronen-Spinat-Pasta', 'lunch', 4, 'vegan',
          E'Ohne Vorlage. Wenige Zutaten, 15 Minuten.\n1. Pasta kochen, 150 ml Kochwasser aufheben. 2. Knoblauch in Olivenöl goldgelb anbraten. 3. Spinat zusammenfallen lassen. 4. Pasta, Kochwasser, Zitronenabrieb und -saft und Hefeflocken zugeben und cremig schwenken. 5. Mit gerösteten Pinienkernen, Salz, Pfeffer und Chiliflocken servieren.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Nudeln',                      400::numeric, 'g',     1),
  ('Spinat',                      200::numeric, 'g',     2),
  ('Zitronen',                      1::numeric, 'Stück',    3),
  ('Knoblauch',                     3::numeric, 'Zehen', 4),
  ('Olivenöl',                      3::numeric, 'EL',    5),
  ('Hefeflocken',                  30::numeric, 'g',     6),
  ('Pinienkerne',                  30::numeric, 'g',     7),
  ('Salz',                       null::numeric, null,    8),
  ('Pfeffer gemahlen',           null::numeric, null,    9),
  ('Chiliflocken',               null::numeric, null,   10)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 18. Suesskartoffel-Katsu-Curry (Miguel Barclay)
-- ---------------------------------------------------------------------------
-- Barclays Rezepte sind durchweg fuer EINE Portion gerechnet. Alle Mengen der
-- Gerichte 18-23 sind deshalb vervierfacht; wo das Original mit "Handvoll"
-- oder "ein paar Scheiben" arbeitet, steht hier eine uebliche Menge fuer vier.
--
-- Zwei Abweichungen, beide in der Notiz genannt: self-raising flour gibt es
-- hier nicht (Mehl + Backpulver), und beim Frittieroel steht der VERBRAUCH und
-- nicht die Topfmenge. 1 l Oel auf die Einkaufsliste zu schreiben waere in der
-- Wochensumme eine Luege - das Oel wird nicht gegessen.
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Süßkartoffel-Katsu-Curry', 'dinner', 4, 'vegan',
          E'Quelle: Miguel Barclay, "Vegan One Pound Meals" (2019), "Sweet Potato Katsu Curry". Original für 1 Portion, hier auf 4 hochgerechnet. Self-raising flour gibt es hier nicht, daher 400 g Mehl + 5 TL Backpulver. Beim Öl steht der Verbrauch: frittiert wird in ca. 3 cm Öl, das meiste bleibt übrig.\n1. Reis mit der doppelten Menge Salzwasser zugedeckt ca. 7 Min. bei mittlerer Hitze garen, bis das Wasser aufgesogen ist, dann mit der Gabel auflockern. 2. Öl ca. 3 cm hoch erhitzen. 3. Aus 400 g Mehl, dem Backpulver, einer Prise Salz und 600 ml kaltem Wasser einen Teig verquirlen. 4. Süßkartoffeln in 1 cm dicke Scheiben schneiden, durch den Teig ziehen und ca. 10 Min. goldbraun und gar frittieren, auf Küchenpapier abtropfen lassen. 5. Für die Sauce 4 TL Mehl, Currypulver und Golden Syrup in der Pfanne zu einer Paste rühren, dann nach und nach ca. 800 ml Wasser einarbeiten und mit Sojasauce abschmecken. 6. Reis, Süßkartoffelscheiben und Sauce anrichten, mit Frühlingszwiebeln bestreuen.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Süßkartoffeln',               800::numeric, 'g',     1),
  -- 410 g: 400 g fuer den Tempurateig, 4 TL fuer die Sauce. Zwei Zeilen
  -- daraus zu machen wuerde die Einkaufsliste ohne Gewinn verdoppeln.
  ('Weizenmehl Type 405',         410::numeric, 'g',     2),
  ('Backpulver',                    5::numeric, 'TL',    3),
  ('Currypulver',                   4::numeric, 'TL',    4),
  ('Golden Syrup',                  7::numeric, 'EL',    5),
  ('Sojasauce',                     4::numeric, 'EL',    6),
  ('Frühlingszwiebeln',             1::numeric, 'Bund',  7),
  ('Rapsöl',                      200::numeric, 'ml',    8),
  ('Basmatireis',                 300::numeric, 'g',     9),
  ('Salz',                       null::numeric, null,   10)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 19. Butternut-Mac & No Cheese (Miguel Barclay)
-- ---------------------------------------------------------------------------
-- Der Kuerbis IST die Sauce, deshalb die grosse Menge. In der Preisliste steht
-- nur der allgemeine "Kuerbis"; welcher gemeint ist, sagt die Notiz.
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Butternut-Mac & No Cheese', 'dinner', 4, 'vegan',
          E'Quelle: Miguel Barclay, "Vegan One Pound Meals" (2019), "Mac & No Cheese". Original für 1 Portion, hier auf 4 hochgerechnet. Gemeint ist Butternutkürbis, ca. 1,5 kg – er ersetzt die Käsesauce. Als Nudeln nimmt das Original Makkaroni.\n1. Ofen auf 190 Grad vorheizen. 2. Kürbis schälen und in 1-cm-Würfel schneiden, mit Olivenöl, Salz und Pfeffer mischen und ca. 30 Min. rösten, bis er weich und leicht karamellisiert ist. 3. Kürbis mit 300 ml Wasser und der Brühe fein pürieren, bei Bedarf mehr Wasser dazu, bis eine dicke Suppe entsteht. 4. Nudeln in Salzwasser al dente kochen und abgießen. 5. Nudeln mit der Kürbissauce mischen, salzen und mit frisch gemahlenem Pfeffer servieren.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Kürbis',                     1500::numeric, 'g',     1),
  ('Nudeln',                      400::numeric, 'g',     2),
  -- 2 Bruehwuerfel, in Gramm, weil die Preiszeile ein Pulverpaeckchen ist.
  ('Gemüsebrühe',                  20::numeric, 'g',     3),
  ('Olivenöl',                      3::numeric, 'EL',    4),
  ('Salz',                       null::numeric, null,    5),
  ('Pfeffer gemahlen',           null::numeric, null,    6)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 20. Sonnengetrocknete-Tomaten-Pasta (Miguel Barclay)
-- ---------------------------------------------------------------------------
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Sonnengetrocknete-Tomaten-Pasta', 'lunch', 4, 'vegan',
          E'Quelle: Miguel Barclay, "Vegan One Pound Meals" (2019), "Sun-Dried Tomato Pasta". Original für 1 Portion, hier auf 4 hochgerechnet. Ein Topf, ca. 12 Minuten – das Öl aus dem Tomatenglas ist die halbe Sauce.\n1. Spaghetti in Salzwasser al dente kochen und den Spinat die letzten 10 Sekunden mit ins Wasser geben. 2. Abgießen und sofort in eine Schüssel geben. 3. Getrocknete Tomaten grob hacken und mit den Pinienkernen und einem guten Schuss Öl aus dem Glas unterheben. 4. Mit Salz und Pfeffer abschmecken.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Spaghetti',                   500::numeric, 'g',     1),
  ('Spinat',                      200::numeric, 'g',     2),
  ('Tomaten getrocknet in Öl',     20::numeric, 'Stück',    3),
  ('Pinienkerne',                  60::numeric, 'g',     4),
  ('Salz',                       null::numeric, null,    5),
  ('Pfeffer gemahlen',           null::numeric, null,    6)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 21. Golden-Syrup-Brokkoli-Nudeln (Miguel Barclay)
-- ---------------------------------------------------------------------------
-- Barclay schreibt "sheet of dried wholewheat noodles", also ein Nudelnest je
-- Portion. In der Preisliste steht das als "Vollkornnudeln".
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Golden-Syrup-Brokkoli-Nudeln', 'dinner', 4, 'vegan',
          E'Quelle: Miguel Barclay, "Vegan One Pound Meals" (2019), "Golden Syrup Broccoli Noodles". Original für 1 Portion, hier auf 4 hochgerechnet. 15 Minuten.\n1. Nudeln nach Packungsangabe in Salzwasser kochen. 2. Brokkoli in Röschen teilen und in einem Schuss Sesamöl 2 Min. anbraten, dann Knoblauch und Chiliflocken dazu und 1-2 Min. weiterbraten. 3. Golden Syrup, Sojasauce, 4 EL Sesamöl, Limettensaft, frisch gemahlenen Pfeffer und 4 EL Nudelwasser zugeben und 2 Min. einkochen. 4. Nudeln abgießen, in die Pfanne geben, durchschwenken und sofort servieren.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Vollkornnudeln',              250::numeric, 'g',     1),
  ('Brokkoli',                      1::numeric, 'Stück',    2),
  ('Knoblauch',                     4::numeric, 'Zehen', 3),
  ('Golden Syrup',                  4::numeric, 'EL',    4),
  ('Sojasauce',                     4::numeric, 'EL',    5),
  ('Sesamöl',                       6::numeric, 'EL',    6),
  ('Chiliflocken',                  1::numeric, 'TL',    7),
  ('Limette',                       2::numeric, 'Stück',    8),
  ('Salz',                       null::numeric, null,    9),
  ('Pfeffer gemahlen',           null::numeric, null,   10)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 22. Zitronige Erbsen auf Ciabatta (Miguel Barclay)
-- ---------------------------------------------------------------------------
-- Steht als 'breakfast' im Katalog, weil es Barclays Antwort auf Avocado-Toast
-- ist. Als Mittagessen funktioniert es genauso.
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Zitronige Erbsen auf Ciabatta', 'breakfast', 4, 'vegan',
          E'Quelle: Miguel Barclay, "Vegan One Pound Meals" (2019), "Lemony Peas on Toast" – sein Ersatz für Avocado-Toast, weil Avocados zu teuer sind. Original für 1 Portion, hier auf 4 hochgerechnet.\n1. Erbsen in kochendes Salzwasser geben und, sobald das Wasser wieder kocht, vom Herd nehmen. 2. Die Hälfte der Erbsen mit ein paar Löffeln Kochwasser und der Brühe zu einem Püree mixen, mit Salz und Pfeffer abschmecken und die restlichen, abgetropften Erbsen unterrühren. 3. Ciabatta in Scheiben schneiden, mit Olivenöl bestreichen, salzen, pfeffern und in einer trockenen Pfanne je Seite ca. 1 Min. rösten, bis sie Farbe haben. 4. Erbsenpüree aufstreichen, mit dünn geschälten Zucchinistreifen belegen, Chiliflocken darüber streuen und Zitrone darüber ausdrücken.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('TK-Erbsen',                   600::numeric, 'g',     1),
  ('Ciabatta',                      2::numeric, 'Stück',    2),
  ('Zucchini',                    200::numeric, 'g',     3),
  ('Zitronen',                      1::numeric, 'Stück',    4),
  ('Gemüsebrühe',                  20::numeric, 'g',     5),
  ('Chiliflocken',                  1::numeric, 'TL',    6),
  ('Olivenöl',                      3::numeric, 'EL',    7),
  ('Salz',                       null::numeric, null,    8),
  ('Pfeffer gemahlen',           null::numeric, null,    9)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 23. Kartoffeln Arrabbiata (Miguel Barclay)
-- ---------------------------------------------------------------------------
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Kartoffeln Arrabbiata', 'dinner', 4, 'vegan',
          E'Quelle: Miguel Barclay, "Vegan One Pound Meals" (2019), "Potatoes Arrabbiata". Original für 1 Portion, hier auf 4 hochgerechnet. Kleine Kartoffeln mit Schale, am Ende leicht zerdrückt – sie ersetzen selbstgemachte Gnocchi.\n1. Kartoffeln mit Schale in Salzwasser ca. 10 Min. weich kochen und abgießen. 2. Knoblauch in Scheiben in Olivenöl bei mittlerer Hitze anbraten, bis er Farbe nimmt. 3. Chiliflocken, passierte Tomaten und die Kartoffeln dazu, kräftig salzen und pfeffern und 10 Min. köcheln. 4. Spinat unterheben und 1 Min. zusammenfallen lassen. 5. Jede Kartoffel mit dem Gabelrücken leicht zerdrücken und servieren.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Kartoffeln',                  800::numeric, 'g',     1),
  ('Passierte Tomaten',           800::numeric, 'g',     2),
  ('Spinat',                      200::numeric, 'g',     3),
  ('Knoblauch',                     4::numeric, 'Zehen', 4),
  ('Chiliflocken',                  1::numeric, 'TL',    5),
  ('Olivenöl',                      3::numeric, 'EL',    6),
  ('Salz',                       null::numeric, null,    7),
  ('Pfeffer gemahlen',           null::numeric, null,    8)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 24. Cremige Tomaten-Basilikum-Suppe (Alice Beckhams)
-- ---------------------------------------------------------------------------
-- Beckhams rechnet in Cups und Ounces; die Mengen der Gerichte 24-27 sind
-- umgerechnet (1 Cup = 240 ml, 1 Cup Haferflocken = 90 g, 1 Cup
-- Kichererbsenmehl = 120 g, 1 Dose 15 oz = 425 g, 1 Dose 28 oz = 795 g).
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Cremige Tomaten-Basilikum-Suppe', 'lunch', 4, 'vegan',
          E'Quelle: Alice Beckhams, "Big Vegan Flavor", "Creamy Tomato Basil Soup with Garlic Croutons". 4 Portionen, Mengen von Cups/Ounces umgerechnet. Das Original nimmt 2 Dosen ganze geschälte Tomaten – hier stückige, die Suppe wird ohnehin püriert.\n1. Zwiebel in 2 EL Olivenöl 5 Min. weich braten, Knoblauch 1 Min. mitbraten. 2. Tomatenmark 1 Min. mitrösten, dann die Tomaten mit Saft zugeben. 3. Brühe, Zucker, Oregano, Chiliflocken, Salz und Pfeffer dazu, aufkochen und 20 Min. köcheln. 4. Fein pürieren, Kokosmilch und die zerrissenen Basilikumblätter einrühren, 5 Min. nachziehen lassen und abschmecken. 5. Für die Croutons Ciabatta würfeln, mit 2 EL Olivenöl, Knoblauch, Salz und Pfeffer mischen und bei 190 Grad 10-12 Min. goldbraun backen, einmal durchschütteln. 6. Suppe anrichten und mit Croutons und Basilikum servieren.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Tomaten stückig',            1600::numeric, 'g',     1),
  ('Gemüsebrühe',                 240::numeric, 'ml',    2),
  ('Kokosmilch',                  240::numeric, 'ml',    3),
  ('Zwiebeln',                      1::numeric, 'Stück',    4),
  -- 3 Zehen fuer die Suppe, 2 fuer die Croutons.
  ('Knoblauch',                     5::numeric, 'Zehen', 5),
  ('Tomatenmark',                   1::numeric, 'EL',    6),
  ('Basilikum frisch',              1::numeric, 'Bund',  7),
  ('Ciabatta',                      1::numeric, 'Stück',    8),
  ('Oregano',                       1::numeric, 'TL',    9),
  ('Chiliflocken',                0.5::numeric, 'TL',   10),
  ('Zucker',                        1::numeric, 'TL',   11),
  ('Olivenöl',                      4::numeric, 'EL',   12),
  ('Salz',                       null::numeric, null,   13),
  ('Pfeffer gemahlen',           null::numeric, null,   14)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 25. Knusprige Ofen-Kichererbsen (Alice Beckhams)
-- ---------------------------------------------------------------------------
-- Das erste 'snack' im Katalog. Cayennepfeffer steht als "Chilipulver" da,
-- weil genau das in der Preisliste die gemahlene scharfe Chili ist.
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Knusprige Ofen-Kichererbsen', 'snack', 4, 'vegan',
          E'Quelle: Alice Beckhams, "Big Vegan Flavor", "Crispy Chickpea Snacks". 4 Portionen, Mengen umgerechnet (1 Dose = 425 g). Auch als Topping auf Salat.\n1. Ofen auf 200 Grad vorheizen, Blech mit Backpapier belegen. 2. Kichererbsen abtropfen, abspülen und mit einem Tuch trocken tupfen – nur trocken werden sie knusprig. 3. Mit Olivenöl, Räucherpaprika, Knoblauchpulver, Kreuzkümmel, Salz, Pfeffer und Chilipulver mischen, bis alles gleichmäßig überzogen ist. 4. In einer Schicht auf dem Blech verteilen und 25-30 Min. backen, dabei einmal durchschütteln. 5. Kurz abkühlen lassen – beim Abkühlen werden sie noch knuspriger.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Kichererbsen',                425::numeric, 'g',     1),
  ('Olivenöl',                      1::numeric, 'EL',    2),
  ('Paprikapulver geräuchert',      1::numeric, 'TL',    3),
  ('Knoblauchpulver',             0.5::numeric, 'TL',    4),
  ('Kreuzkümmel',                 0.5::numeric, 'TL',    5),
  ('Chilipulver',                0.25::numeric, 'TL',    6),
  ('Salz',                          null::numeric, null,    7),
  ('Pfeffer gemahlen',              null::numeric, null,    8)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 26. Kichererbsen-Pfannkuchen mit Ahornsirup (Alice Beckhams)
-- ---------------------------------------------------------------------------
-- Vanilleextrakt gibt es in der Preisliste nicht und im Haushalt vermutlich
-- auch nicht; ein Paeckchen Vanillezucker tut hier dasselbe.
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Kichererbsen-Pfannkuchen mit Ahornsirup', 'breakfast', 2, 'vegan',
          E'Quelle: Alice Beckhams, "Big Vegan Flavor", "Fluffy Chickpea Pancakes with Maple Syrup". 2 Portionen (ca. 6 Pfannkuchen), Mengen umgerechnet. Statt 1 TL Vanilleextrakt ein Päckchen Vanillezucker. Angerührt wird mit Wasser, ein Pflanzendrink geht genauso.\n1. Kichererbsenmehl, Backpulver und Salz in einer Schüssel verrühren. 2. 240 ml Wasser mit Öl, 1 EL Ahornsirup und dem Vanillezucker mischen. 3. Die feuchte Mischung langsam unter Rühren zur trockenen geben, bis der Teig glatt und klümpchenfrei ist, dann 5-10 Min. stehen lassen – er zieht dabei an. 4. Beschichtete Pfanne bei mittlerer Hitze leicht einölen, je Pfannkuchen ca. 60 ml Teig eingießen und je Seite 2-3 Min. backen, bis die Oberfläche Bläschen zieht und die Ränder gar aussehen. 5. Gestapelt mit dem restlichen Ahornsirup, Blaubeeren und gehackten Walnüssen servieren.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Kichererbsenmehl',            120::numeric, 'g',     1),
  ('Backpulver',                    1::numeric, 'TL',    2),
  ('Vanillezucker',                 8::numeric, 'g',     3),
  ('Ahornsirup',                    4::numeric, 'EL',    4),
  ('Olivenöl',                      1::numeric, 'EL',    5),
  ('Blaubeeren',                  125::numeric, 'g',     6),
  ('Walnüsse',                     30::numeric, 'g',     7),
  ('Salz',                       null::numeric, null,    8)
) as z(name, menge, einheit, sortierung);

-- ---------------------------------------------------------------------------
-- 27. Overnight Oats mit Mandelmus (Alice Beckhams)
-- ---------------------------------------------------------------------------
with gericht as (
  insert into dishes (household_id, name, default_meal_type, default_servings, category, note)
  values (:'haushalt'::uuid, 'Overnight Oats mit Mandelmus', 'breakfast', 2, 'vegan',
          E'Quelle: Alice Beckhams, "Big Vegan Flavor", "Overnight Oats with Almond Butter". 2 Portionen, Mengen umgerechnet. Keine Kochzeit, aber eine Nacht Vorlauf.\n1. Haferflocken, Mandelmilch, Chiasamen, Ahornsirup und Vanillezucker in einem Glas mit Deckel verrühren. 2. Verschlossen über Nacht, mindestens 4-6 Std., in den Kühlschrank. 3. Morgens umrühren; ist die Masse zu dick, einen Schuss Mandelmilch dazu. 4. Mit Mandelmus, Bananenscheiben, gehackten Walnüssen und Zimt belegen. 5. Kalt oder zimmerwarm essen.')
  on conflict (household_id, lower(name)) do update
    set default_meal_type = excluded.default_meal_type,
        default_servings  = excluded.default_servings,
        category          = excluded.category,
        note              = excluded.note,
        updated_at        = now()
  returning id
), aufraeumen as (
  delete from dish_ingredients where dish_id in (select id from gericht)
)
insert into dish_ingredients (household_id, dish_id, name, quantity, unit, sort_order)
select :'haushalt'::uuid, gericht.id, z.name, z.menge, z.einheit, z.sortierung
from gericht, (values
  ('Haferflocken',                 90::numeric, 'g',     1),
  ('Mandelmilch',                 240::numeric, 'ml',    2),
  ('Chiasamen',                     1::numeric, 'EL',    3),
  ('Ahornsirup',                    1::numeric, 'TL',    4),
  ('Vanillezucker',                 8::numeric, 'g',     5),
  ('Mandelmus',                     2::numeric, 'EL',    6),
  ('Bananen',                     200::numeric, 'g',     7),
  ('Walnüsse',                     15::numeric, 'g',     8),
  ('Zimt',                       null::numeric, null,    9)
) as z(name, menge, einheit, sortierung);

commit;

-- Kontrollausgabe: was steht jetzt im Katalog?
select d.name, d.default_meal_type, d.default_servings, d.category,
       (select count(*) from dish_ingredients i where i.dish_id = d.id) as zutaten
from dishes d
where d.household_id = :'haushalt'::uuid
order by d.name;

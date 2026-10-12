-- Nacharbeit zu scripts/zutatenpreise_aus_scrape.sql: die Zutaten, die nach
-- dem Preisabgleich noch ohne Preis dastanden.
--
-- Aufruf:
--   docker exec -i finanzplaner-db psql -U postgres -d postgres \
--       -v haushalt=<household-uuid> -f - < zutaten_aliase_und_korrekturen.sql
--
-- Drei Faelle, drei verschiedene Antworten - deshalb liegt das hier und nicht
-- im Preisskript:
--
--   1. Es gibt das Produkt doch, die automatische Wortsuche hat es nur nicht
--      gefunden (frischer Koriander, veganer Reibekaese, Kichererbsenmehl).
--      -> Preiszeile, von Hand aus derselben Scrape-Datei herausgesucht.
--   2. Tippfehler im Zutatennamen eines alten Gerichts. -> Name korrigieren.
--      Eine Preiszeile auf den Tippfehler anzulegen wuerde ihn festschreiben.
--   3. Dieselbe Zutat, andere Schreibweise als in der Preisliste ("Milch"
--      gegen "Vollmilch"). -> Alias. Das Rezept behaelt seine Wortwahl, der
--      Preis wird trotzdem gefunden.
--
-- Was hier NICHT steht: Bockshornklee und Curryblaetter. Die kommen in der
-- Preisdatei schlicht nicht vor - weder bei REWE noch bei dm oder Mueller.
-- Ein geschaetzter Preis waere in der Wochensumme nicht von einem echten zu
-- unterscheiden, deshalb bleiben sie leer.

\set ON_ERROR_STOP on
set client_encoding = 'UTF8';

begin;

-- ---------------------------------------------------------------------------
-- 1. Nachgetragene Preise (Median der Sichtungen aus latest-canonical.csv)
-- ---------------------------------------------------------------------------
insert into ingredient_prices
  (household_id, name, price_minor, pack_quantity, pack_unit, note, price_source)
values
  (:'haushalt'::uuid, 'Koriander frisch',  89,  25.00, 'g',
   'reweDe: Koriander 25g (Median aus 3 Sichtungen)', 'manual'),
  (:'haushalt'::uuid, 'Streukäse vegan',  229, 200.00, 'g',
   'reweDe: Simply V Reibegenuss vegan 200g (Median aus 3 Sichtungen)', 'manual'),
  (:'haushalt'::uuid, 'Kichererbsenmehl', 175, 300.00, 'g',
   'dmDe: dmBio Kichererbsenmehl 300g (Median aus 3 Sichtungen)', 'manual')
on conflict (household_id, lower(name)) do nothing;

-- ---------------------------------------------------------------------------
-- 2. Tippfehler in Zutatennamen
-- ---------------------------------------------------------------------------
update dish_ingredients set name = 'Chiliflocken', updated_at = now()
where household_id = :'haushalt'::uuid and name = 'Chilliflocken';

update dish_ingredients set name = 'Kichererbsenmehl', updated_at = now()
where household_id = :'haushalt'::uuid and name = 'Kirchererbensmehl';

-- ---------------------------------------------------------------------------
-- 3. Schreibweisen-Zuordnungen
-- ---------------------------------------------------------------------------
-- alias_key/price_key sind lower(btrim(...)) - genau die Form, mit der
-- grocery_pricing_service.load_price_lookup nachschlaegt.
insert into ingredient_price_aliases
  (household_id, alias_key, alias_name, price_key, price_name)
values
  (:'haushalt'::uuid, 'milch',      'Milch',      'vollmilch', 'Vollmilch'),
  (:'haushalt'::uuid, 'brot',       'Brot',       'mischbrot', 'Mischbrot'),
  (:'haushalt'::uuid, 'mais (dose)','Mais (Dose)','mais',      'Mais')
on conflict (household_id, alias_key) do nothing;

commit;

-- Kontrolle: was hat jetzt noch keinen Preis?
select coalesce(string_agg(distinct i.name, ', ' order by i.name), 'alles bepreist')
       as ohne_preis
from dish_ingredients i
left join ingredient_prices p on lower(btrim(p.name)) = lower(btrim(i.name))
left join ingredient_price_aliases a on a.household_id = i.household_id
                                    and a.alias_key = lower(btrim(i.name))
where p.id is null and a.id is null and i.household_id = :'haushalt'::uuid;

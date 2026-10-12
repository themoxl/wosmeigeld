-- Einheiten im Gerichte-Katalog vereinheitlichen.
--
-- Aufruf:
--   docker exec -i finanzplaner-db psql -U postgres -d postgres \
--       -v haushalt=<household-uuid> -f - < zutaten_einheiten_vereinheitlichen.sql
--
-- Hintergrund: dieselbe Zutat stand auf der Einkaufsliste mehrfach, weil die
-- Wochenzusammenfassung nur addiert, was sie sicher addieren kann (siehe
-- grocery_service._merge_key). Der groesste Teil davon war ein Codefehler -
-- die Zusammenfassung verglich den Rohtext der Einheit, obwohl die Anwendung
-- "St", "Stk" und "Stueck" laengst als dieselbe Einheit kennt. Das ist
-- behoben.
--
-- Hier bleiben die Faelle, die kein Code entscheiden kann, weil sie eine
-- Aussage ueber das Lebensmittel verlangen:
--
--   1. Knoblauch in "St" gegen "Zehen" - eine Knolle ist nicht eine Zehe.
--   2. Ingwer und Tomaten mal in Gramm, mal in Stueck.
--   3. Salz und Pfeffer mal mit Menge, mal ohne.
--
-- Nicht angefasst: EL gegen TL bei Gewuerzen. Das sind echte
-- Rezeptunterschiede (1 EL Kreuzkuemmel ist nicht 1 TL), keine Schlampigkeit.
-- Sie zusammenzufuehren braucht Umrechnungen (ingredient_unit_conversions),
-- und die sind ausdruecklich nicht Teil dieser Aufraeumaktion.

\set ON_ERROR_STOP on
set client_encoding = 'UTF8';

begin;

-- ---------------------------------------------------------------------------
-- 1. Schreibweisen (rein kosmetisch - die Anwendung sieht sie ohnehin gleich)
-- ---------------------------------------------------------------------------
update dish_ingredients set unit = 'Stück', updated_at = now()
where household_id = :'haushalt'::uuid and unit in ('St', 'Stk');

update dish_ingredients set unit = 'Pkg', updated_at = now()
where household_id = :'haushalt'::uuid and unit = 'Packung';

-- ---------------------------------------------------------------------------
-- 2. Knoblauch: ueberall in Zehen
-- ---------------------------------------------------------------------------
-- Andersherum waere es falsch: "3 Stück Knoblauch" sind drei Knollen, und der
-- Preis je Knolle wuerde die Woche um das Zehnfache verteuern. Betroffen ist
-- eine einzige Zeile ("Knoblauch 1 Stück" im Linsenburger), und dort ist eine
-- Zehe die einzige plausible Lesart.
update dish_ingredients set unit = 'Zehen', updated_at = now()
where household_id = :'haushalt'::uuid
  and lower(btrim(name)) = 'knoblauch'
  and unit is distinct from 'Zehen';

-- ---------------------------------------------------------------------------
-- 3. Ingwer und Tomaten: ueberall in Gramm
-- ---------------------------------------------------------------------------
-- Gramm gewinnt, weil die Preisliste beides nach Gewicht fuehrt. Die beiden
-- Stueckangaben werden dabei zu einem Gewicht - das ist eine Schaetzung
-- (ein daumengrosses Stueck Ingwer ~ 20 g, eine mittlere Tomate ~ 100 g) und
-- steht deshalb als Notiz an der Zutat.
update dish_ingredients
set quantity = 20, unit = 'g', note = 'Stückangabe als 20 g angesetzt', updated_at = now()
where household_id = :'haushalt'::uuid
  and lower(btrim(name)) = 'ingwer'
  and unit in ('Stück', 'St', 'Stk');

update dish_ingredients
set quantity = quantity * 100, unit = 'g', note = 'Stückangabe mit 100 g je Tomate angesetzt',
    updated_at = now()
where household_id = :'haushalt'::uuid
  and lower(btrim(name)) = 'tomaten'
  and unit in ('Stück', 'St', 'Stk');

-- ---------------------------------------------------------------------------
-- 4. Curryblätter: "Handvoll" gibt es nur einmal
-- ---------------------------------------------------------------------------
update dish_ingredients set quantity = 15, unit = 'Stück', updated_at = now()
where household_id = :'haushalt'::uuid
  and lower(btrim(name)) = 'curryblätter'
  and unit = 'Handvoll';

-- ---------------------------------------------------------------------------
-- 5. Salz und Pfeffer: ohne Menge
-- ---------------------------------------------------------------------------
-- Beides wird abgeschmeckt, nicht abgewogen, und beides steht ohnehin im
-- Schrank. Eine Mengenangabe an der einen und keine an der anderen Stelle
-- erzeugt nur zwei Einkaufszeilen fuer dieselbe Packung Salz.
update dish_ingredients set quantity = null, unit = null, updated_at = now()
where household_id = :'haushalt'::uuid
  and lower(btrim(name)) in ('salz', 'pfeffer gemahlen', 'pfeffer')
  and (quantity is not null or unit is not null);

commit;

-- Kontrolle: welche Zutat hat jetzt noch uneinheitliche Einheiten?
select i.name || ': ' || string_agg(distinct coalesce(i.unit, '(ohne)'), ' + ')
from dish_ingredients i
where i.household_id = :'haushalt'::uuid
group by lower(btrim(i.name)), i.name
having count(distinct coalesce(i.unit, '(ohne)')) > 1
order by 1;

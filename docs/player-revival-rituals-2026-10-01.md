# Player-requested revival rituals

Players can now request the six established ritual methods previously used only
by the background simulation. These are campaign house rules with dice checks;
the existing Revivify spell still follows its separate component and slot rules.

Use `revival options` to list local providers and recorded dead. Then use
`revival <person> with <caster>` (also `revive` or `resurrect`) to get an offer.
The offer shows the exact gold cost, one-hour duration, caster bonus, DC,
success consequence, and resources lost on failure. Nothing is spent until
`confirm revival`; `cancel revival` discards the offer.

The body and provider must be present. The caster must be alive, non-hostile,
capable, and explicitly established as a service provider with reserved materials
and charges. A destroyed site, active combat, or a nearby living hostile prevents
the ritual. Soul and body requirements still apply, and the death must remain
within the method's window when the hour ends.

Both outcomes spend the quoted player gold, the provider's materials and one
charge, and advance the clock by an hour. A provider needs a full day after the
ritual to recover. Charges and materials do not refill on reload. Payment is
recorded as provider income, separate from material stock; cash cannot silently
replace components. A returned character has 1 HP and a recorded consequence.

Offers persist across reloads but expire after one game hour. Confirmation
revalidates participants, location, latest death, soul state, caster strength,
price and supplies. Changed terms require a new offer. Repeat confirmations are
harmless; a failed new quote clears an old offer. Database failures roll back
payment, stock, life state, and offer consumption together.

Newly seeded Kingsward gives High Priestess Auriel a 750 gp divine service, with
one charge and 500 gp of reserved supplies. Newly seeded The Veil gives the Fey
Archon a 1,500 gp fey service, with one charge and 1,000 gp of supplies. Existing
NPCs receive the new service in a one-time migration; existing budgets, cooldowns,
refusals and life state are preserved. Other methods use the same service
contract when explicitly supplied by authored content.

Offers belong to a save and are included in timeline snapshots. Rewinding a
snapshot from before the offer table existed clears future offers.

This does not yet implement arbitrary new player-authored rituals, corpse
transport, all named resurrection spells, or automatic quests enforcing ritual
debts. Those need their own validated effects. A generated claim that an NPC is
a powerful healer does not grant resurrection authority.

# Slicing exemplars — good vs. bad, same feature

Consult these when a slice is borderline or fails the test. Each example slices one feature both ways: the **vertical** cut ships a demoable behavior on day one and grows behavior-by-behavior; the **horizontal/hybrid** cut builds disconnected layers that demo nothing until a final "wire it all up" phase. The three features are deliberately different shapes — transactional, event-driven, data/query.

---

## Exemplar 1 — Checkout & payment

*A shopper reviews a cart, enters shipping + payment, and places an order that charges a card and emails a receipt.*

**Vertical (correct) — walking skeleton first**

1. **Place a free order for one in-stock item** — *Given a cart with one $0 in-stock item, When the shopper clicks Place Order, Then an Order (status PLACED) is created and a confirmation page shows the order number.*
   → Walking skeleton: thinnest end-to-end path with a visible outcome; carries the minimal Order/OrderLine model **inside** the behavior — no payment, no email.
2. **Charge a card and place a paid order** — *Given a cart with a $40 item and a valid test card, When the shopper submits payment, Then the card is charged via the gateway and the Order is saved PAID with the charge id.*
   → End-to-end across a new layer (payment gateway); shippable even with no email/shipping.
3. **Reject a declined card without creating an order** — *Given a card the gateway declines, When the shopper submits payment, Then no Order is persisted, the cart is preserved, and an inline "card declined" error shows.*
   → A failure path is its own vertical slice — demoable, and it ships the safety behavior that prevents phantom orders.
4. **Ship a paid order to a captured address** — *Given a paid order, When a shipping address was entered, Then it is validated, stored on the Order, and shown on confirmation.*
5. **Email a receipt for a placed order** — *Given a PAID order, When it is placed, Then a receipt email is sent and a "receipt sent" note appears on confirmation.*

**Horizontal / hybrid (wrong) — each trips a smell**

- **Set up the checkout database schema and models** → foundation-first trap + layer-noun title (*Schema, Models*).
- **Build the payment-gateway integration layer** → layer-noun title (*Integration*); proves no scenario.
- **Implement the checkout REST API endpoints** → single-layer slice (all controllers) + layer-noun title (*API*).
- **Build the checkout UI screens** → single-layer slice (all UI) + layer-noun title (*UI*).
- **Integrate and wire up the full checkout flow** → terminal integrate phase (proves the rest were horizontal).

*Contrast: a working order on day one, grown one behavior at a time — vs. four disconnected layers that demo nothing until a risky final "wire it all up".*

---

## Exemplar 2 — In-app notifications

*Users receive, view, mark-as-read, and configure delivery preferences for activity notifications (mentions, replies, system alerts).*

**Vertical (correct) — walking skeleton first**

1. **See a mention notification in the bell** — *Given Bob mentions Alice in a comment, When Alice opens the app, Then she sees one unread "Bob mentioned you" in her bell dropdown with a badge of 1.*
   → Walking skeleton: event → persisted Notification → API → bell UI; carries the minimal Notification model **inside** the behavior.
2. **Mark a notification as read** — *Given an unread notification, When Alice clicks it, Then it loses unread styling, the badge drops 1→0, and it stays read after refresh.*
   → Independently shippable; a visible badge change a non-dev recognizes.
3. **Get notified of a reply** — *Given Carol replies to Alice's thread, When Alice opens the app, Then a "Carol replied" notification appears alongside her mentions.*
   → Generalizes the path to a second event type **as a vertical slice** — not a horizontal "add all event types" phase.
4. **Mute reply notifications** — *Given Alice turns off "Replies" in settings, When Carol replies, Then no notification is created and the bell is unchanged; mentions still arrive.*
   → A preference delivered end-to-end (toggle UI → stored preference → suppression at creation), not a "settings layer".

**Horizontal / hybrid (wrong) — each trips a smell**

- **Notifications database schema and models** → foundation-first trap + layer-noun title.
- **Notifications backend API layer** → single-layer slice + layer-noun title (*Backend, API*).
- **Notification bell UI components** → single-layer slice (mocked data) + layer-noun title (*UI*).
- **Event ingestion plumbing for mentions and replies** → layer-noun title (*Plumbing*); demos nothing alone.
- **Integrate and wire everything together** → terminal integrate phase.

*Contrast: a working mention-in-the-bell on day one — vs. four dead layers that reveal whether anything works only at the end.*

---

## Exemplar 3 — Saved searches on a list view

*A user filters a list, saves the filter as a named search, re-runs it later, and gets notified when new matches appear.*

**Vertical (correct) — walking skeleton first**

1. **Save the current filter as a named search** — *Given I filtered the orders list to status=open, region=EU, When I click Save and name it "Open EU orders", Then it appears in my Saved searches list.*
   → Walking skeleton: carries the saved_search model (name + serialized filter + owner) **inside** a visible behavior.
2. **Re-run a saved search** — *Given I saved "Open EU orders", When I click it, Then the list reloads showing exactly status=open, region=EU.*
   → End-to-end (UI → deserialize/apply filter → query); valuable even if notifications never ship.
3. **Show how many new matches appeared since I last ran it** — *Given I last ran it Monday and 3 new matches exist, When I open Saved searches, Then a "3 new" badge shows on it.*
   → One observable behavior end-to-end, adding one `last_run_at` column **inside** the behavior — not a schema phase.
4. **Email me when new matches appear** — *Given I enabled notifications on a saved search, When a new match is created, Then I receive an email listing it.*
   → The richest behavior last; depends on 1–3 but adds its own end-to-end value.

**Horizontal / hybrid (wrong) — each trips a smell**

- **Saved-search database schema and models** → foundation-first trap + layer-noun title.
- **Saved-search REST API** → single-layer slice + layer-noun title (*API*).
- **Saved-search UI components** → single-layer slice (mocked data) + layer-noun title (*UI*).
- **Notification infrastructure** → foundation-first + layer-noun title (*Infrastructure*).
- **Wire it all together and integrate end-to-end** → terminal integrate phase.

*Contrast: a working save-and-rerun on day one, grown behavior by behavior — vs. schema/API/UI/infra built in isolation, demoable only after a final "wire it all up".*

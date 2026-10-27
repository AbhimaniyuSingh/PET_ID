# Privacy & security decisions

PetID puts a pet's details on a public page that anyone holding the QR can open, so every
design decision starts from: *what is the least a stranger needs to see to get the pet home?*

## What a finder can see
Always: name, species, age, area/city, and the contact form.
Only if the owner allows it (per-field switches): vaccination record, medical/food notes and vet,
map of the home area, microchip number, and the owner's phone number.
Defaults: phone **hidden**, microchip **hidden**, everything else shown.

## Decisions and why
| Risk | What the app does |
|---|---|
| Phone number scraped from public pages | Hidden by default. Finders use a form; the owner gets an email + SMS and calls back. |
| Home location revealed | Only a locality is asked for ("don't enter your street"), and the map shows that area. |
| Someone guessing other pets' pages | Page addresses use random 8-character codes, not 1, 2, 3… |
| Lost or leaked tag | Owner can **regenerate** the QR; the old code immediately stops working. |
| Account takeover | Passwords are hashed (never stored); login attempts are rate-limited; reset links expire in 1 hour and work once. |
| Forged requests (CSRF) | Every form carries a secret token, checked on every POST. A test fails if any form lacks one. |
| Editing someone else's pet | Every pet page checks the pet belongs to the logged-in user (others get "not found"). |
| Bots flooding owners with SMS | Honeypot field + maths question + per-IP rate limits + a per-pet alert cooldown. |
| Fake vaccination records | Only admin-approved vets can verify; a verified record loses its tick if the owner changes the vaccine or date. |
| Session theft / fixation | HttpOnly, SameSite cookies, Secure on https, new session at login. |
| Malicious uploads | Only PNG/JPG/WEBP that open as real images, max 5 MB, renamed to random names. |
| Referrer leakage to the map | `Referrer-Policy: strict-origin-when-cross-origin` header. |
| Old database after an upgrade | Moved to a backup file instead of being deleted or crashing. |

## Known gaps (honest list)
- No email verification at sign-up, no two-factor login.
- In-memory rate limiting resets on restart and isn't shared across servers.
- Photos of pets are public to anyone with the tag link.
- Third parties (email/SMS providers, Google Maps) receive the data needed to do their job.
- Community animals are listed by neighbourhood; this is visible to everyone by design.

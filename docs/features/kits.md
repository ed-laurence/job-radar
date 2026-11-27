# Application kits (streamed)

`POST /roles/{id}/kit` streams a kit as Server-Sent Events.
- `parts` defaults to all of `cv`, `cover_note`, `outreach` and `answers`.
- `questions` holds application-form questions to answer.

## How it streams
- Each part is one streamed model call (feature `kit`). The listing, the ad and the candidate profile go in as delimited data blocks, never in the system prompt.
- Text is released as `segment` events (a sentence or paragraph, tagged with `part`) only after it passes the redaction filter: secret patterns, the denylist, PII, then the output guard.
- If the filter later finds that a released segment held part of a secret, a `retract` event replaces it with `[redacted]`. The stored kit holds exactly the filtered text.
- The stream ends with `done` (`result_id` is the role id, `status` is `ok` or `needs_review`) or with an `error` event carrying an RFC 9457 problem.

## Quality bar, checked in code (ported from job-seeker)
- The cover letter is 250-650 words, starts with "Dear", and ends with "Kind regards," and the profile name.
- Outreach is at most 300 characters.
- Screening answers are present when they were requested.
- No banned stock phrases and no em or en dashes.
- British/Australian spelling when the profile's `spelling` asks for it.
- No figures that aren't in the profile or the ad.

Failed checks give `needs_review` and a `kit_regenerated` event that names the number of failures.

## Other rules
- Regenerating some parts keeps the others.
- Only one kit per role can be generating at a time (409).

## Downloads
- `GET /roles/{roleId}/kit/cover-letter.pdf?download=true|false` (`getKitCoverLetterPdf`): the
  kit's cover letter as one A4 page in job-seeker's layout (2 cm margins, Helvetica, name 18 pt
  bold, contact line, date like "27 November 2026" in the display time zone, "To" from
  `letter_to` else "Hiring Team, <company>", a bold "Re:" from `letter_re` else the title,
  `**bold**` lead-ins; the body type shrinks to 9 pt to fit). Name and contact come from the
  profile settings (`name`, `location`, `email`, else the login email). File
  `<Name>_Cover_Letter_<Company>.pdf`. 404 before a cover letter exists.
- `GET /roles/{roleId}/kit/cv.pdf?download=` (`getKitCvPdf`): the stored CV for the role's track
  (leadership for `exec`, else technical, else whichever is uploaded), as `<Name>_CV.pdf`. 404
  when none is uploaded. job-seeker's in-place CV tweaks are not ported.
- `download=false` gives an inline Content-Disposition. Responses are `no-store`.

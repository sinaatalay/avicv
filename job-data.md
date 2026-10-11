**Start with free employer APIs plus Himalayas.** Add Adzuna or JSearch when you want broader US/Canada search; use scraping for specific gaps.

I completed **52 HTTP observations**, saved **3,227 normalized listing records**, and spent **$0**. The sample includes other countries and occupations; it is not a count of US/Canada tech openings.

The [full research report](/Users/sina/conductor/workspaces/speedhack/calgary-v1/.context/job-data-research/REPORT.md) includes pricing, working requests, restrictions, measured results, and saved evidence.

**The free options that actually worked**

| Source | What I tested | My assessment |
|---|---|---|
| **Greenhouse** | Stripe: 728 jobs; Cloudflare: 424; Airbnb: 161 | Excellent for following known employers. Full descriptions and application links; no key needed. |
| **Ashby** | Ashby: 68; Notion: 132; Linear: 31 | Particularly useful schema: descriptions, remote fields, locations, compensation where supplied. |
| **Lever** | PolicyMe: 9; BlueCat: 30; Veeva: 200 sampled | Good employer-level access, with pagination. |
| **SmartRecruiters** | ServiceNow: 200 sampled; Ubisoft: 100 sampled | Works publicly, but full descriptions required separate detail requests. |
| **Himalayas** | Browsing, US/Canada keyword searches, pagination, country restrictions | **My first free choice for discovering remote jobs across companies.** |
| **Jobicy, Remote OK, Remotive, WWR** | Retrieved listings from every source | Useful supplementary feeds; their coverage and publication windows vary. |
| **Arbeitnow** | 638 distinct records across two pages | Works, but its geographic mix makes it less useful for your target market. |

The main catch with employer APIs: **you need the company’s current board identifier**. They do not provide a universal search across employers.

Two requests you can run immediately:

```bash
# Employer listings, including descriptions and available salary ranges
curl -fsS 'https://boards-api.greenhouse.io/v1/boards/airbnb/jobs?content=true&pay_transparency=true'

# Remote software-engineering jobs explicitly listing Canada
curl -fsS 'https://himalayas.app/jobs/api/search?q=software%20engineer&country=CA&exclude_worldwide=true&sort=recent&page=1'
```

See the official [Greenhouse documentation](https://docs.greenhouse.io/job-board.html) and [Himalayas documentation](https://himalayas.app/api).

**Free keys and paid options**

Published prices checked October 11, 2026; USD.

| Provider | Starting allowance / price | Useful for |
|---|---|---|
| [Adzuna](https://developer.adzuna.com/docs/terms_of_service) | Default 2,500 requests/month; additional daily/weekly limits | Broad search for personal research. Descriptions are snippets. |
| [JSearch](https://www.openwebninja.com/api/jsearch) | 200 requests/month free; $25/month for 10,000 | My first inexpensive broad-search trial. |
| [SerpApi](https://serpapi.com/pricing) | 250 searches/month free; $25/month for 1,000 | Retrieving Google Jobs search results. |
| [TheirStack](https://theirstack.com/en/pricing?tab=api) | $49/month for 1,500 API credits | A searchable job database; currently one returned job per credit. |
| [Coresignal](https://coresignal.com/pricing/) | Seven-day, 2,000-credit trial; $49/month for 2,500 credits | Structured records and larger data pulls; endpoint charges need attention. |

**I did not benchmark authenticated paid data.** I checked documentation and some authentication boundaries, but used no accounts or keys. These are candidates to evaluate, not proven coverage winners.

**The most useful empirical findings**

- **Request options matter:** Airbnb’s ordinary Greenhouse response exposed no structured salary ranges. Adding `pay_transparency=true` exposed ranges for **127 of the same 161 jobs**.
- **“Remote” needs interpretation:** Himalayas’ Canada search included worldwide listings. Its strict-country option changed the sample from **15/20 explicitly listing Canada to 20/20**.
- **An empty response can mislead:** several old company identifiers returned empty lists or 404. Find the current careers page before concluding there are no openings.
- **Deduplication is necessary:** Arbeitnow’s two pages overlapped by 12 IDs; WWR repeated one identifier.
- **Canadian historical data exists:** I also downloaded and parsed 52,368 Job Bank historical job-location rows. The report explains why these differ from live application listings.

**Should you scrape?** Selectively. Prefer a documented API, then RSS, then embedded `JobPosting` JSON-LD, then HTML extraction. My sampled Lever page contained usable JSON-LD; the sampled Greenhouse and SmartRecruiters HTML pages did not. I would avoid starting with LinkedIn/Indeed scrapers given maintenance costs and their [LinkedIn](https://www.linkedin.com/legal/user-agreement) / [Indeed](https://www.indeed.com/legal) access restrictions.

Everything is saved in `.context/job-data-research`:

- [Full analysis](/Users/sina/conductor/workspaces/speedhack/calgary-v1/.context/job-data-research/REPORT.md)
- [Scripts and rerun instructions](/Users/sina/conductor/workspaces/speedhack/calgary-v1/.context/job-data-research/README.md)
- [Collected listings, JSONL](/Users/sina/conductor/workspaces/speedhack/calgary-v1/.context/job-data-research/results/jobs.normalized.jsonl)

No tracked application files were changed.

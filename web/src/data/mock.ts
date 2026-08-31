// Mock reference data for screens not yet backed by the Python pipeline.
// Ported from the design prototype (data.js). Mode Monitor / Weekly Digest uses
// the live backend instead; these power the supporting reference screens.

export const watchlist = [
  { co: 'BHP', tier: 'A', sector: 'Mining', region: 'AU', monthRoles: 78, trend: 'up', note: 'Maintenance ramp Newman; capex steady' },
  { co: 'Rio Tinto', tier: 'A', sector: 'Mining', region: 'AU', monthRoles: 65, trend: 'flat', note: 'New VP Ops Pilbara appointed' },
  { co: 'Newmont', tier: 'A', sector: 'Mining', region: 'PNG', monthRoles: 28, trend: 'up', note: 'Significant hiring spike — investigate' },
  { co: 'Barrick', tier: 'A', sector: 'Mining', region: 'PNG', monthRoles: 10, trend: 'flat', note: 'Porgera restart steady' },
  { co: 'TotalEnergies', tier: 'A', sector: 'Oil & Gas', region: 'PNG', monthRoles: 3, trend: 'flat', note: 'Pre-FID; contractor pre-qual activity' },
  { co: 'ExxonMobil', tier: 'A', sector: 'Oil & Gas', region: 'PNG', monthRoles: 8, trend: 'flat', note: 'PNG LNG operational maintenance' },
  { co: 'Glencore', tier: 'A', sector: 'Mining', region: 'AU', monthRoles: 19, trend: 'flat', note: 'McArthur shutdown signal (Sep)' },
  { co: 'Downer', tier: 'A', sector: 'Construction', region: 'AU', monthRoles: 34, trend: 'up', note: 'Inland Rail win driving demand' },
  { co: 'Monadelphous', tier: 'A', sector: 'Construction', region: 'AU', monthRoles: 22, trend: 'up', note: 'Karratha shutdown crews' },
  { co: 'Ok Tedi', tier: 'A', sector: 'Mining', region: 'PNG', monthRoles: 11, trend: 'flat', note: 'Tabubil operations steady' },
  { co: 'Vale', tier: 'B', sector: 'Mining', region: 'PNG', monthRoles: 3, trend: 'flat', note: 'Indicator' },
  { co: 'Eramet', tier: 'B', sector: 'Mining', region: 'NC', monthRoles: 6, trend: 'up', note: 'Q3 maintenance shutdown signal' },
  { co: 'Saipem', tier: 'B', sector: 'Oil & Gas', region: 'PNG', monthRoles: 12, trend: 'flat', note: 'Possible Papua LNG positioning' },
  { co: 'Technip', tier: 'B', sector: 'Oil & Gas', region: 'SG', monthRoles: 4, trend: 'flat', note: 'APAC hub steady' },
  { co: 'Subsea7', tier: 'B', sector: 'Oil & Gas', region: 'SG', monthRoles: 5, trend: 'up', note: 'Offshore wind tendering' },
  { co: 'Aurecon', tier: 'B', sector: 'Construction', region: 'AU', monthRoles: 14, trend: 'up', note: 'Mining project engineering' },
  { co: 'Vinci', tier: 'B', sector: 'Construction', region: 'AU', monthRoles: 6, trend: 'down', note: 'Below average — monitor' },
  { co: 'Thales', tier: 'C', sector: 'Defence', region: 'AU', monthRoles: 14, trend: 'flat', note: 'Hunter-class workstream' },
  { co: 'Safran', tier: 'C', sector: 'Defence', region: 'AU', monthRoles: 5, trend: 'flat', note: 'MRH-90 sustainment' },
  { co: 'Eiffage', tier: 'C', sector: 'Construction', region: 'AU', monthRoles: 8, trend: 'flat', note: 'Civil works indicator' },
] as const

export const matches = [
  { rank: 1, co: 'BHP — Newman', score: 94, rel: 'TIER A · ACTIVE CLIENT', region: 'AU', sector: 'Mining', evidence: ['7 maintenance roles posted this week', 'Shutdown preparation pattern detected', 'Easy Skill placed 4 maintenance roles at Newman in 2025'], action: 'Send MPC email' },
  { rank: 2, co: 'Newmont Lihir', score: 89, rel: 'TIER A · ACTIVE CLIENT', region: 'PNG', sector: 'Mining', evidence: ['Hiring surge (+150% vs avg) includes Maintenance Planner', 'Process Engineer debrief confirms expansion', 'Consultant has 3 yrs prior PNG experience'], action: 'Send MPC email' },
  { rank: 3, co: 'Monadelphous — Karratha', score: 78, rel: 'NEW NAME', region: 'AU', sector: 'Mining services', evidence: ['8 maintenance roles, contractor to BHP/Rio sites', 'No prior Easy Skill engagement — net-new potential'], action: 'Cold outreach' },
  { rank: 4, co: 'Eramet — Doniambo', score: 71, rel: 'TIER B', region: 'NC', sector: 'Mining', evidence: ['Client call: Q3 maintenance shutdown 10–15 contractors', 'French-speaking advantage'], action: 'Account manager intro' },
] as const


export const tokens = {
  weekIn: 1_842_300,
  weekOut: 184_220,
  weekCost: 11.84,
  monthCost: 142.18,
  perAgent: [
    { agent: 'Data Collector', model: 'Haiku', calls: 1242, cost: 4.21, share: 35 },
    { agent: 'Signal Analyst', model: 'Gemini Flash', calls: 348, cost: 5.18, share: 44 },
    { agent: 'Report Generator', model: 'Gemini Pro', calls: 32, cost: 1.92, share: 16 },
    { agent: 'Profile Matcher', model: 'Gemini Flash', calls: 11, cost: 0.53, share: 5 },
  ],
} as const

export const hist = {
  AU: [612, 644, 671, 698, 715, 724, 730, 758, 780, 791, 820, 847],
  PNG: [22, 25, 24, 26, 28, 27, 30, 31, 29, 32, 33, 34],
  SECTORS: {
    Mining: 690,
    Construction: 162,
    'Oil & Gas': 38,
    Defence: 15,
    'Energy Transition': 12,
  },
} as const

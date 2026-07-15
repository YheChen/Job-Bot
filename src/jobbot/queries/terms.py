"""Vocabulary used to build search queries.

These are defaults. Guild settings can extend locations / academic terms /
keywords at runtime via Discord commands.
"""

from __future__ import annotations

# Grouped so we can pick semantically-related titles into a single OR-clause
# rather than exploding the Cartesian product.
JOB_TITLE_GROUPS: dict[str, list[str]] = {
    "generic_swe": [
        "software engineer intern",
        "software engineering intern",
        "software developer intern",
        "software development intern",
        "SWE intern",
        "developer intern",
    ],
    "backend": ["backend engineer intern", "backend developer intern"],
    "frontend": [
        "frontend engineer intern",
        "front end engineer intern",
    ],
    "fullstack": ["full stack engineer intern", "full-stack engineer intern"],
    "mobile": [
        "mobile engineer intern",
        "iOS engineer intern",
        "Android engineer intern",
    ],
    "platform_infra": [
        "platform engineer intern",
        "infrastructure engineer intern",
        "cloud engineer intern",
        "DevOps intern",
        "site reliability engineer intern",
        "SRE intern",
    ],
    "data_ml": [
        "data engineer intern",
        "machine learning engineer intern",
        "ML engineer intern",
        "AI engineer intern",
    ],
    "security": ["security engineer intern"],
    "embedded": [
        "embedded software intern",
        "firmware engineer intern",
        "systems software intern",
    ],
    "other": [
        "game developer intern",
        "QA automation intern",
        "test engineer intern",
    ],
}

# Category tags applied to jobs and offered as user subscriptions.
TITLE_GROUP_TO_CATEGORY: dict[str, str] = {
    "generic_swe": "software",
    "backend": "backend",
    "frontend": "frontend",
    "fullstack": "fullstack",
    "mobile": "mobile",
    "platform_infra": "infrastructure",
    "data_ml": "machine_learning",
    "security": "security",
    "embedded": "embedded",
    "other": "software",
}

INTERNSHIP_TERMS: list[str] = [
    "intern",
    "internship",
    "co-op",
    "coop",
    "student",
    "summer intern",
    "winter intern",
    "fall intern",
    "spring intern",
]

DEFAULT_ACADEMIC_TERMS: list[str] = [
    "Summer 2027",
    "Winter 2027",
    "Spring 2027",
    "Fall 2027",
    "2027 internship",
    "2027 co-op",
]

DEFAULT_LOCATIONS: list[str] = [
    "Toronto",
    "Canada",
    "United States",
    "New York",
    "San Francisco",
    "Seattle",
    "Vancouver",
    "Waterloo",
    "Remote",
    "North America",
]

# ATS platforms. slug -> (display name, search domain)
PLATFORMS: dict[str, tuple[str, str]] = {
    "ashby": ("Ashby", "jobs.ashbyhq.com"),
    "greenhouse": ("Greenhouse", "boards.greenhouse.io"),
    "greenhouse_jb": ("Greenhouse", "job-boards.greenhouse.io"),
    "lever": ("Lever", "jobs.lever.co"),
    "workday": ("Workday", "myworkdayjobs.com"),
    "workday_alt": ("Workday", "workdayjobs.com"),
    "smartrecruiters": ("SmartRecruiters", "jobs.smartrecruiters.com"),
    "smartrecruiters_careers": ("SmartRecruiters", "careers.smartrecruiters.com"),
    "workable": ("Workable", "apply.workable.com"),
    "jobvite": ("Jobvite", "jobs.jobvite.com"),
    "bamboohr": ("BambooHR", "jobs.bamboohr.com"),
    "icims": ("iCIMS", "jobs.icims.com"),
    "careerspage": ("Careers-Page", "careers-page.com"),
    "recruitee": ("Recruitee", "recruitee.com"),
    "personio": ("Personio", "jobs.personio.com"),
    "rippling": ("Rippling", "ats.rippling.com"),
    "adp": ("ADP", "careers.adp.com"),
    "oracle": ("Oracle Cloud", "oraclecloud.com"),
    "successfactors": ("SuccessFactors", "successfactors.com"),
}

# 003 – DOMAIN MODEL

Version: 0.1 (Draft)

Purpose
The Domain Model defines the canonical business entities of Roster.

Core Entities:
- Business
- Customer
- Contact
- Property
- Equipment
- Conversation
- Appointment
- Job
- Workflow
- Decision
- Policy
- Event

Key Principles:
- Business owns customers.
- Customer owns relationships, not phone numbers.
- Properties are independent from customers.
- Conversations never become business truth.
- Workflows own state.
- Events are immutable.
- Policies are versioned.
- AI owns none of the business entities; it reasons over them.

Ownership:
Business, Customer, Contact, Property, Equipment -> Identity
Conversation -> Communication
Appointment, Job, Workflow -> Workflow Engine
Decision -> Decision Runtime
Policy -> Policy Engine
Event -> Event Platform

Canonical Rules:
1. Identity exists independently of communication.
2. Conversations never become truth.
3. Events are append-only.
4. Workflow owns process state.
5. Every entity has exactly one owner.
6. Future features extend existing entities rather than creating duplicates.

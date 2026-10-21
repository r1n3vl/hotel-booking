# Hotel Booking System

Author: Yaroslav Kuzmenkov

Repository: https://github.com/r1n3vl/hotel-booking

## Project overview

A university web application for finding hotel rooms and managing reservations. Users can register, log in, recover their password, filter hotels and rooms, check availability and create bookings. Managers maintain hotel and room records and confirm or cancel reservations.

Stack: Python 3.12, Django 5.2.18, PostgreSQL 17, Django templates, CSS and JavaScript with fetch. Docker Compose runs the application with Gunicorn, WhiteNoise and Mailpit.

## Roles and business rules

CUSTOMER: browse catalogues, book rooms and manage their own eligible reservations.

MANAGER: customer features plus hotel, room and booking management.

ADMIN: application access and Django administration for users and permissions.

Registration always assigns CUSTOMER. Protected pages require authentication and server-side permissions. Forms validate input, and customers cannot access other users’ bookings.

A booking must have valid dates and fit the room capacity. The server calculates the total from the number of nights and a stored nightly price. PENDING and CONFIRMED bookings block overlapping stays; CANCELLED bookings release availability. Transactions and row locks protect application booking creation against concurrent requests.

## Data model and dataflow

The main relationships are Hotel → Room → Booking, with each booking also linked to a User. Referenced historical records are protected from deletion.

The browser submits filters or a booking form to Django. Django checks authentication, permissions and input, then reads catalogues or saves a booking in PostgreSQL. Django returns an HTML page or an AJAX JSON response to the browser. Password reset emails are sent to Mailpit.

Catalogue filters use GET requests and AJAX to update results without reloading the whole page. Booking submissions use POST; the server checks availability inside a transaction before saving. Password reset links are delivered to the local Mailpit inbox. Responsive CSS supports desktop and smaller screens.

## Docker startup

Start Docker Desktop. In the project directory, create .env from .env.example if it does not already exist, then set your secret key and database credentials. Keep existing credentials when reusing a database volume.

```powershell
docker compose --env-file .env up -d --build --wait
docker compose ps
```

Application: http://127.0.0.1:8081/

Mailpit: http://127.0.0.1:8025/

Host Python is not required. Startup runs migrations and collects static files. Database records persist in the PostgreSQL volume; do not delete it when troubleshooting.

## Testing

Automated tests cover authentication, permissions, validation, filters, booking rules, concurrency and password recovery. GitHub Actions runs checks, tests and Docker smoke tests with a minimum Python application statement coverage of 70%.

The reviewed implementation is commit 4f7212e, whose CI run passed. sDeployment on the second Windows computer and current desktop/mobile appearance still require confirmation. Interface screenshots are not included in this file.

## AI usage

ChatGPT and Codex helped explain unfamiliar topics, such as AJAX and Docker. They provided step-by-step explanations and examples to support understanding of how these parts work together. AI also helped with parts of the HTML/CSS layout and responsive styling, and assisted with implementation, tests and troubleshooting. Suggested changes were checked against the project requirements and available automated checks; remaining verification limits are stated above.

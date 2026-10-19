"""Publish only the two simple exercises, without changing accounts or history."""
import argparse
import os
import ssl

from seed_demo import Client, ensure_simple_scenarios, ensure_assignments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--group-id', help='Optionally assign both exercises to an existing teacher group')
    args = parser.parse_args()
    username = os.environ.get('TRAINING_TEACHER_USERNAME')
    password = os.environ.get('TRAINING_TEACHER_PASSWORD')
    if not username or not password:
        raise SystemExit('Set TRAINING_TEACHER_USERNAME and TRAINING_TEACHER_PASSWORD in ENV')
    teacher = Client(args.base_url)
    # Never inherit the historical demo seeder's certificate bypass.
    teacher.context = ssl.create_default_context(cafile=os.environ.get('TRAINING_CA_FILE') or None)
    teacher.call('POST', '/auth/login', {'username': username, 'password': password})
    identifiers = ensure_simple_scenarios(teacher)
    if args.group_id:
        titles = {item['id']: item['title'] for item in teacher.catalog()}
        ensure_assignments(teacher, args.group_id, identifiers, titles)
    print('Simple DDS exercises ready:', ', '.join(identifiers))


if __name__ == '__main__':
    main()
